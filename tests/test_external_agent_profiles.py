from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path

import pytest

from paulsha_hippo.agent_profiles import (
    AgentProfile,
    ExternalAgentRouter,
    ProfileConfigError,
    FIXED_PER_CHUNK_DEADLINE_SECONDS,
    FIXED_SESSION_DEADLINE_CAP_SECONDS,
    FIXED_SESSION_DEADLINE_SECONDS,
    cache_identity,
    child_environment,
    default_profiles,
    session_deadline_seconds,
)
from paulsha_hippo.atomizer.agent_exec import CachingAgentClient

STUB = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"


def _profile(
    profile_id: str,
    *,
    tier: int = 1,
    priority: int = 1,
    argv: tuple[str, ...] | None = None,
    max_session_chunks: int | None = None,
) -> AgentProfile:
    raw: dict[str, object] = {
        "id": profile_id,
        "tier": tier,
        "priority": priority,
        "traits": ["test"],
        "task_classes": ["atomization", "title"],
        "model": "test-model",
        "effort": "medium",
        "supported_efforts": ["low", "medium", "high"],
        "argv": list(argv or (sys.executable, str(STUB))),
    }
    if max_session_chunks is not None:
        raw["max_session_chunks"] = max_session_chunks
    return AgentProfile.from_mapping(raw)


def _agent_profile_kwargs(
    profile_id: str = "task-class-probe",
    *,
    max_session_chunks: int | object = None,
    tier: int = 1,
    priority: int = 1,
) -> dict[str, object]:
    raw = {
        "id": profile_id,
        "tier": tier,
        "priority": priority,
        "traits": ["test"],
        "task_classes": ["atomization", "title"],
        "model": "test-model",
        "effort": "medium",
        "supported_efforts": ["low", "medium", "high"],
        "argv": [sys.executable, str(STUB)],
    }
    if max_session_chunks is not None:
        raw["max_session_chunks"] = max_session_chunks
    return raw


def test_session_deadline_scales_with_chunk_count():
    assert session_deadline_seconds(1) == FIXED_SESSION_DEADLINE_SECONDS
    assert session_deadline_seconds(2) == FIXED_SESSION_DEADLINE_SECONDS
    assert session_deadline_seconds(7) == min(
        FIXED_SESSION_DEADLINE_CAP_SECONDS,
        max(
            FIXED_SESSION_DEADLINE_SECONDS,
            7 * FIXED_PER_CHUNK_DEADLINE_SECONDS,
        ),
    )
    assert session_deadline_seconds(100) == FIXED_SESSION_DEADLINE_CAP_SECONDS


def test_per_call_timeout_still_bounded_by_fixed_timeout_seconds():
    captured: list[int] = []

    def run_one(profile, prompt, call_index, timeout):  # noqa: ARG001
        captured.append(timeout)
        return "valid", "", 0

    router = ExternalAgentRouter((_profile("only", tier=1),))
    router._run_one = run_one  # type: ignore[method-assign]
    # 9 monotonic() reads per this 1-profile/2-chunk session: started, the
    # deadline/circuit checks, attempt_started, then per chunk a shared
    # chunk_started/remaining_seconds read plus one elapsed-time read after
    # the call returns (issue #86 per-chunk provenance), and finally the
    # attempt's own elapsed-time read.
    time_points = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 450.0, 450.0, 450.0])

    original_monotonic = time.monotonic
    time.monotonic = lambda: next(time_points)  # type: ignore[method-assign]
    try:
        assert router.run_session(("chunk-0", "chunk-1"), response_validator=lambda raw: raw == "valid") == (
            "valid",
            "valid",
        )
    finally:
        time.monotonic = original_monotonic

    assert captured == [300, 150]


def test_router_rejects_invalid_max_session_chunks():
    raw = _agent_profile_kwargs("invalid-zero", max_session_chunks=0)
    with pytest.raises(ProfileConfigError, match="max_session_chunks"):
        AgentProfile.from_mapping(raw)
    raw = _agent_profile_kwargs("invalid-negative", max_session_chunks=-1)
    with pytest.raises(ProfileConfigError, match="max_session_chunks"):
        AgentProfile.from_mapping(raw)
    raw = _agent_profile_kwargs("invalid-float", max_session_chunks=2.5)
    with pytest.raises(ProfileConfigError, match="max_session_chunks"):
        AgentProfile.from_mapping(raw)
    raw = _agent_profile_kwargs("invalid-bool", max_session_chunks=True)
    with pytest.raises(ProfileConfigError, match="max_session_chunks"):
        AgentProfile.from_mapping(raw)


def test_profile_max_session_chunks_defaults_to_none_and_is_keyword_only():
    profile = _profile("no-limit")
    assert profile.max_session_chunks is None
    with pytest.raises(TypeError):
        profile.eligible("atomization", 7)  # type: ignore[call-arg]


def test_eligibility_obeys_max_session_chunks():
    profile = _profile("limited", tier=1, max_session_chunks=6)
    assert profile.eligible(task_class="atomization", chunk_count=7) == (
        False,
        "session_size",
    )
    assert profile.eligible(task_class="atomization", chunk_count=6) == (True, "eligible")
    assert profile.eligible(task_class="atomization", chunk_count=None) == (True, "eligible")


def test_router_skips_profile_with_oversized_session_without_call():
    oversized = _profile("tier1-small", tier=1, max_session_chunks=1)
    accept = _profile("tier2", tier=2)
    calls: list[str] = []

    def execute(profile, prompt, attempt):
        calls.append(profile.id)
        return "answer", "", 0

    router = ExternalAgentRouter((oversized, accept), executor=execute, max_agent_calls=10)
    assert router.run_session(tuple("0123456")) == tuple("answer" for _ in range(7))
    assert calls == ["tier2"] * 7
    assert [a.profile_id for a in router.attempts] == ["tier1-small", "tier2"]
    assert router.attempts[0].failure_category == "ineligible"
    assert router.attempts[0].profile_id == "tier1-small"


def test_default_profiles_have_max_session_chunks_bounds():
    profiles = default_profiles()
    mapping = {profile.id: profile.max_session_chunks for profile in profiles}
    assert mapping["claude"] == 6
    assert mapping["codex"] == 6
    assert mapping["cg"] is None


def test_default_profiles_have_three_deterministic_tiers_and_traits():
    profiles = default_profiles()
    assert [(profile.id, profile.tier) for profile in profiles] == [
        ("claude", 1), ("codex", 1), ("agy", 2), ("cg", 2), ("co-gem", 3), ("claude-gem", 3)
    ]
    assert all(profile.task_classes for profile in profiles)
    assert all(profile.provider_context >= 32768 for profile in profiles)
    claude, codex, agy = profiles[:3]
    assert ("--tools", "") in tuple(zip(claude.argv, claude.argv[1:]))
    assert "--safe-mode" in claude.argv
    assert "--reasoning-effort" not in codex.argv
    assert "model_reasoning_effort=high" in codex.argv
    assert "atomization" not in agy.task_classes
    assert [profile.id for profile in profiles if profile.enabled] == [
        "claude", "codex", "agy"
    ]
    assert codex.supported_efforts == ("high",)


def test_default_claude_profile_does_not_run_in_plan_permission_mode():
    """Plan mode contradicts the frozen single-JSON output contract.

    Claude Code's plan mode is an approval workflow (explore → design → plan
    file → ExitPlanMode); asked to atomize a real session it answers with prose
    asking how to proceed instead of emitting the contract document, so the
    parser sees no JSON at all.  Measured on a 48-fragment parked session:
    606 bytes of prose containing neither ``{`` nor ``[`` with the flag, versus
    22,778 bytes of valid schema-1 JSON with the flag removed and every other
    argument identical.  Write protection is unaffected — it comes from
    ``--tools ""`` (claude CLI 2.1.220: "Use \"\" to disable all tools"), not
    from the permission mode.
    """
    claude = default_profiles()[0]
    pairs = tuple(zip(claude.argv, claude.argv[1:]))
    assert ("--permission-mode", "plan") not in pairs
    # The restrictions that actually bound the external agent stay in place.
    assert ("--tools", "") in pairs
    for flag in ("--safe-mode", "--disable-slash-commands", "--strict-mcp-config"):
        assert flag in claude.argv


@pytest.mark.parametrize(
    "argv",
    [
        ("claude", "--permission-mode", "plan", "--print"),
        ("claude", "--permission-mode=plan", "--print"),
        ("claude", "--permission-mode", "PLAN", "--print"),
    ],
)
def test_profile_rejects_plan_permission_mode(argv):
    with pytest.raises(ProfileConfigError, match="plan"):
        _profile("plan-mode", argv=argv)


@pytest.mark.parametrize(
    "argv",
    [
        ("claude", "--permission-mode", "bypassPermissions", "--print"),
        ("claude", "--permission-mode=bypassPermissions", "--print"),
    ],
)
def test_profile_rejects_permission_bypass_mode(argv):
    """``bypassPermissions`` is the flag-value spelling of an already-forbidden
    token (``--dangerously-skip-permissions``) and must be rejected alike."""
    with pytest.raises(ProfileConfigError, match="permission bypass"):
        _profile("bypass-mode", argv=argv)


def test_packaged_config_template_argv_matches_canonical_defaults():
    """The shipped template must not drift from the canonical profile argv.

    Every profile command lives in two repo surfaces (this module's defaults and
    ``atomizer.yaml``); a fix applied to one and missed in the other ships the
    defect anyway, which is how the plan-mode argument survived unnoticed.
    """
    import yaml

    template_path = (
        Path(__file__).resolve().parents[1] / "paulsha_hippo" / "atomizer" / "atomizer.yaml"
    )
    document = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    shipped = {
        entry["id"]: tuple(entry["argv"])
        for entry in document["external_agents"]["profiles"]
    }
    assert shipped == {profile.id: profile.argv for profile in default_profiles()}


def test_packaged_config_template_max_session_chunks_matches_canonical_defaults():
    import yaml

    template_path = (
        Path(__file__).resolve().parents[1] / "paulsha_hippo" / "atomizer" / "atomizer.yaml"
    )
    document = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    shipped = {
        entry["id"]: entry.get("max_session_chunks")
        for entry in document["external_agents"]["profiles"]
    }
    assert shipped == {profile.id: profile.max_session_chunks for profile in default_profiles()}


@pytest.mark.parametrize("category", ["policy", "config", "schema", "unsafe", "not-a-category"])
def test_fallback_policy_accepts_only_immutable_transition_allowlist(category):
    profile = _profile("fallback-policy")
    raw = {**vars(profile), "fallback_on": [category]}
    with pytest.raises(ProfileConfigError):
        AgentProfile.from_mapping(raw)


@pytest.mark.parametrize(
    "argv",
    [
        ("bash", "-c", "echo x"),
        ("copilot", "--autopilot", "-p", "{PROMPT}"),
        ("copilot", "--autopilot=true"),
        ("copilot", "--dangerously-skip-permissions"),
        ("copilot", "--model={MODEL}"),
        ("/bin/bash", "--version"),
    ],
)
def test_profile_rejects_shell_prompt_and_permission_bypass(argv):
    with pytest.raises(ProfileConfigError):
        _profile("unsafe", argv=argv)


def test_profile_rejects_option_shaped_model_and_effort_values():
    base = {
        "id": "unsafe-value",
        "tier": 1,
        "priority": 1,
        "traits": ["test"],
        "task_classes": ["atomization"],
        "model": "--help",
        "supported_models": ["--help"],
        "effort": "medium",
        "supported_efforts": ["medium"],
        "argv": [sys.executable, str(STUB), "{MODEL}", "{EFFORT}"],
    }
    with pytest.raises(ProfileConfigError, match="unsafe value"):
        AgentProfile.from_mapping(base)


def test_minimal_environment_does_not_inherit_parent_or_accept_credentials(monkeypatch):
    monkeypatch.setenv("PRIVATE_AGENT_SECRET", "must-not-cross")
    env = child_environment({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2048"})
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2048"
    assert "PRIVATE_AGENT_SECRET" not in env
    with pytest.raises(ProfileConfigError):
        child_environment({"AGENT_API_KEY": "no"})


def test_disabled_profile_is_ineligible():
    raw = {
        "id": "disabled", "tier": 1, "priority": 1, "traits": ["test"],
        "task_classes": ["atomization"], "model": "m", "effort": "medium",
        "supported_efforts": ["medium"], "argv": [sys.executable, str(STUB)],
        "enabled": False,
    }
    profile = AgentProfile.from_mapping(raw)
    assert profile.eligible() == (False, "disabled")


def test_eligibility_uses_service_effective_path(tmp_path):
    executable = tmp_path / "profile-agent"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    profile = _profile("service-agent", argv=("profile-agent",))
    assert profile.eligible(path=str(tmp_path)) == (True, "eligible")
    assert profile.eligible(path="/definitely/missing") == (False, "executable")


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ("authentication failed; login required", "auth"),
        ("quota exceeded", "quota"),
        ("model overloaded", "capacity"),
        ("context length exceeded", "context_capability"),
        ("network connection failed", "transport"),
        ("unknown failure", "process"),
    ],
)
def test_failure_classification_is_bounded(stderr, category):
    from paulsha_hippo.agent_profiles import classify_failure

    assert classify_failure(stderr) == category


def test_sanitize_stderr_redacts_secret_that_spans_the_truncation_boundary():
    # Codex 複驗 BLOCKING：sanitize_stderr 舊行為「先截斷（limit=500）才交給
    # 下游 processing.sanitize_error_text 做 secret redaction」——若 secret 跨越
    # 第 500 字元邊界，截斷會把 token 腰斬成低於 policy/secrets.yaml github_pat
    # 規則最小長度（ghp_ 後 20+ chars）的殘片，令 regex 比對不到，殘片明文
    # 落入 runtime/queue/_failed/*.json。redaction 必須先於截斷（比照
    # ledger.processing.sanitize_error_text 既有契約），本測試釘住這個順序。
    from paulsha_hippo.agent_profiles import sanitize_stderr

    token = "ghp_" + "A1b2C3d4" * 5  # 44 字元的 GitHub PAT 形狀
    raw = "x" * 490 + " " + token + " trailing"

    sanitized = sanitize_stderr(raw)

    assert "ghp_" not in sanitized
    assert "A1b2C3d4" not in sanitized


def test_router_falls_back_by_tier_and_marks_degraded_success():
    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[str] = []

    def execute(profile, prompt, attempt):
        calls.append(profile.id)
        if profile.id == "first":
            raise RuntimeError("temporarily unavailable")
        return "answer", "", 0

    router = ExternalAgentRouter(profiles, executor=execute)
    assert router.run("prompt") == "answer"
    assert calls == ["first", "second"]
    assert router.last_result is not None
    assert router.last_result.fallback_reason == "degraded-success"
    assert router.attempts[0].failure_category == "process"


def test_enabled_ineligible_profile_is_retained_without_consuming_agent_call(tmp_path):
    missing = _profile(
        "missing",
        tier=1,
        argv=(str(tmp_path / "not-installed"),),
    )
    available = _profile("available", tier=2)
    calls: list[str] = []

    def execute(profile, prompt, attempt):
        calls.append(profile.id)
        return "answer", "", 0

    router = ExternalAgentRouter((missing, available), executor=execute)

    assert router.run("prompt") == "answer"
    assert calls == ["available"]
    assert [attempt.profile_id for attempt in router.attempts] == ["missing", "available"]
    assert router.attempts[0].failure_category == "ineligible"
    assert router.last_result is not None
    assert router.last_result.fallback_reason == "degraded-success"


def test_router_policy_failure_does_not_fallback():
    from paulsha_hippo.agent_profiles import AgentRunError

    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[str] = []

    def execute(profile, prompt, attempt):
        calls.append(profile.id)
        raise AgentRunError("unsafe contract", category="policy")

    router = ExternalAgentRouter(profiles, executor=execute)
    with pytest.raises(AgentRunError, match="fallback exhausted"):
        router.run("same frozen prompt")
    assert calls == ["first"]
    assert len(router.attempts) == 1


def test_router_reuses_exact_frozen_prompt_and_bounds_calls():
    profiles = tuple(
        _profile(f"p{index}", tier=index, priority=1)
        for index in (1, 2, 3)
    )
    prompts: list[str] = []

    def execute(profile, prompt, attempt):
        prompts.append(prompt)
        raise RuntimeError("unavailable")

    router = ExternalAgentRouter(
        profiles, executor=execute, max_attempts=2, max_agent_calls=2
    )
    with pytest.raises(Exception, match="fallback exhausted"):
        router.run("immutable input")
    assert prompts == ["immutable input", "immutable input"]
    assert len(router.attempts) == 2


def test_router_session_resumes_from_first_unvalidated_chunk():
    """Issue #86 / D3: a validated prefix must survive a profile transition.

    Profile A validates chunk 0 and 1, then fails at chunk 2.  Profile B must
    receive only chunk 2 -- not the whole frozen prompt sequence -- and the
    final outputs must be the retained A0/A1 plus B's chunk 2, not a full
    re-run by B.
    """
    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[tuple[str, str, int]] = []

    def execute(profile, prompt, call):
        calls.append((profile.id, prompt, call))
        if profile.id == "first" and prompt == "chunk-2":
            return "malformed", "", 0
        return f"{profile.id}:{prompt}", "", 0

    def validate(raw):
        if raw.endswith("malformed"):
            raise ValueError("response schema mismatch")

    router = ExternalAgentRouter(profiles, executor=execute)
    outputs = router.run_session(
        ("chunk-0", "chunk-1", "chunk-2"), response_validator=validate
    )
    assert outputs == ("first:chunk-0", "first:chunk-1", "second:chunk-2")
    assert calls == [
        ("first", "chunk-0", 1),
        ("first", "chunk-1", 2),
        ("first", "chunk-2", 3),
        ("second", "chunk-2", 4),
    ]
    assert router.last_result is not None
    assert router.last_result.profile_id == "second"
    assert router.last_result.fallback_reason == "degraded-success"
    assert router.attempts[0].failure_category == "invalid_output"


def test_router_session_records_chunk_provenance_for_mixed_profile_session():
    """Issue #86 / D3: provenance SHALL identify the producing profile per chunk."""
    from paulsha_hippo.agent_profiles import AgentRunResult

    profiles = (_profile("first", tier=1), _profile("second", tier=2))

    def execute(profile, prompt, call):
        if profile.id == "first" and prompt == "chunk-2":
            return "malformed", "", 0
        return "valid", "", 0

    def validate(raw):
        if raw != "valid":
            raise ValueError("response schema mismatch")

    router = ExternalAgentRouter(profiles, executor=execute)
    router.run_session(("chunk-0", "chunk-1", "chunk-2"), response_validator=validate)

    assert len(router.chunk_provenance) == 3
    assert all(isinstance(entry, AgentRunResult) for entry in router.chunk_provenance)
    assert [entry.profile_id for entry in router.chunk_provenance] == [
        "first",
        "first",
        "second",
    ]
    # A session completed by multiple profiles must not read as one profile's work.
    assert router.last_result.fallback_reason == "degraded-success"


def test_router_session_single_profile_completion_has_uniform_chunk_provenance():
    """A session with no fallback transition must not look mixed."""
    profile = _profile("only", tier=1)

    def execute(profile, prompt, call):
        return "valid", "", 0

    router = ExternalAgentRouter((profile,), executor=execute)
    router.run_session(("chunk-0", "chunk-1"))

    assert [entry.profile_id for entry in router.chunk_provenance] == ["only", "only"]
    assert router.last_result.fallback_reason is None


def test_router_session_shares_call_budget_across_fallback_restarts():
    """Retention must not reset or refill the session-wide call budget (redline #6).

    Profile A validates chunk 0, then fails on chunk 1.  Retention means B
    only needs to redo chunk 1 and then produce chunk 2 -- but the session's
    call budget is shared, not reset per profile: A already spent 2 of the 3
    available calls, so B gets exactly one more call (chunk 1) before the
    shared budget is exhausted partway through B's own attempt on chunk 2.
    This proves the budget carries over across the profile transition
    instead of being refilled for the fallback profile.
    """
    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[tuple[str, str]] = []

    def execute(profile, prompt, call):
        calls.append((profile.id, prompt))
        if profile.id == "first" and prompt == "chunk-1":
            return "bad", "", 0
        return "valid", "", 0

    def validate(raw):
        if raw != "valid":
            raise ValueError("invalid")

    router = ExternalAgentRouter(
        profiles,
        executor=execute,
        max_agent_calls=3,
    )
    with pytest.raises(Exception, match="fallback exhausted"):
        router.run_session(
            ("chunk-0", "chunk-1", "chunk-2"), response_validator=validate
        )
    assert calls == [
        ("first", "chunk-0"),
        ("first", "chunk-1"),
        ("second", "chunk-1"),
    ]
    assert router.attempts[-1].failure_category == "budget"


def test_router_uses_one_effective_path_for_eligibility_and_execution(tmp_path):
    executable = tmp_path / "path-agent"
    executable.write_text("#!/bin/sh\ncat >/dev/null\nprintf answer\n", encoding="utf-8")
    executable.chmod(0o755)
    profile = _profile("path-agent", argv=("path-agent",))
    router = ExternalAgentRouter(
        (profile,),
        execution_path=str(tmp_path),
    )
    assert router.run("prompt") == "answer"


def test_router_preserves_process_category_and_bounded_exit_evidence():
    from paulsha_hippo.agent_profiles import AgentRunError

    def execute(profile, prompt, call):
        raise AgentRunError(
            "external CLI failed",
            category="process",
            exit_code=3,
            stderr="Bearer do-not-persist",
        )

    router = ExternalAgentRouter((_profile("process", tier=1),), executor=execute)
    with pytest.raises(AgentRunError) as ctx:
        router.run("prompt")
    assert ctx.value.category == "process"
    assert router.attempts[0].exit_code == 3
    assert "exit 3" in str(ctx.value)
    assert "do-not-persist" not in router.attempts[0].stderr


def test_cache_identity_is_profile_specific():
    first = _profile("first")
    second = _profile("second")
    common = {"operation": "atomize", "config_hash": "c", "skill_hash": "s", "prompt_hash": "p"}
    assert cache_identity(profile=first, **common) != cache_identity(profile=second, **common)
    assert cache_identity(profile=first, **common, response_schema="2") != cache_identity(
        profile=first, **common, response_schema="1"
    )


def test_cache_namespace_includes_max_session_chunks():
    limited = _profile("first", max_session_chunks=6)
    unlimited = _profile("first")
    assert ExternalAgentRouter((limited,)).cache_namespace() != ExternalAgentRouter((unlimited,)).cache_namespace()


def test_router_cache_envelope_preserves_fallback_provenance(tmp_path):
    profiles = (_profile("first", tier=1), _profile("second", tier=2))

    def execute(profile, prompt, attempt):
        if profile.id == "first":
            raise RuntimeError("first unavailable")
        return "answer", "", 0

    first_router = ExternalAgentRouter(profiles, executor=execute)
    cached = CachingAgentClient(first_router, tmp_path)
    assert cached.run_cached("frozen prompt", "bound-key") == "answer"
    cache_payload = json.loads(cached.cache_path_for_key("bound-key").read_text())
    assert cache_payload["cache_schema"] == "2"
    assert cache_payload["response_schema"] == "1"
    assert cache_payload["provenance"]["fallback_reason"] == "degraded-success"
    assert len(cache_payload["attempts"]) == 2

    def should_not_execute(profile, prompt, attempt):
        raise AssertionError("cache hit must not launch an agent")

    second_router = ExternalAgentRouter(profiles, executor=should_not_execute)
    replay = CachingAgentClient(second_router, tmp_path)
    assert replay.run_cached("changed prompt text is not part of this identity", "bound-key") == "answer"
    assert replay.last_result is not None
    assert replay.last_result.fallback_reason == "degraded-success"
    assert len(second_router.attempts) == 2


def test_typed_router_rejects_legacy_raw_cache_and_rewrites_envelope(tmp_path):
    profiles = (_profile("first", tier=1),)
    calls = {"n": 0}

    def execute(profile, prompt, attempt):
        calls["n"] += 1
        return "answer", "", 0

    router = ExternalAgentRouter(profiles, executor=execute)
    cached = CachingAgentClient(router, tmp_path)
    cache_path = cached.cache_path_for_key("legacy-key")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("legacy raw answer", encoding="utf-8")

    assert cached.run_cached("prompt", "legacy-key") == "answer"
    assert calls["n"] == 1
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["cache_schema"] == "2"
    assert payload["response_schema"] == "1"


def test_session_cache_lands_per_chunk_and_isolates_profiles(tmp_path):
    """Issue #86 / D3 3.3: a chunk lands the moment it validates -- callers do
    not wait for the whole session -- and each chunk's cache envelope is
    attributed to the profile that actually produced it, not the profile that
    happened to finish the session."""
    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[tuple[str, str]] = []

    def execute(profile, prompt, attempt):
        calls.append((profile.id, prompt))
        if profile.id == "first" and prompt == "chunk-1":
            return "bad", "", 0
        return "valid", "", 0

    def validate(raw):
        if raw != "valid":
            raise ValueError("invalid")

    keys = ("claude:s1__" + "a" * 64, "claude:s1__" + "b" * 64)
    first_router = ExternalAgentRouter(profiles, executor=execute)
    cached = CachingAgentClient(first_router, tmp_path)
    assert cached.run_session(
        ("chunk-0", "chunk-1"),
        cache_keys=keys,
        response_validator=validate,
        response_schema="1",
    ) == ("valid", "valid")
    # chunk-0 is retained from "first"; only chunk-1 is re-tried by "second".
    assert calls == [
        ("first", "chunk-0"),
        ("first", "chunk-1"),
        ("second", "chunk-1"),
    ]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert len(payloads) == 2
    assert all(payload["response_schema"] == "1" for payload in payloads)

    assert len(cached.last_cache_keys) == 2
    chunk0_payload = json.loads(cached.cache_path_for_key(cached.last_cache_keys[0]).read_text())
    chunk1_payload = json.loads(cached.cache_path_for_key(cached.last_cache_keys[1]).read_text())
    # No cross-profile pollution: each chunk's envelope names its own producer.
    assert chunk0_payload["provenance"]["profile_id"] == "first"
    assert chunk1_payload["provenance"]["profile_id"] == "second"

    # A mixed-profile session is a conservative miss on full-session replay
    # (tasks.md 3.3 only requires per-chunk landing/isolation, not a mixed
    # replay); the session genuinely re-executes rather than silently
    # combining stale single-profile candidate caches.
    replay_calls: list[tuple[str, str]] = []

    def replay_execute(profile, prompt, attempt):
        replay_calls.append((profile.id, prompt))
        if profile.id == "first" and prompt == "chunk-1":
            return "bad", "", 0
        return "valid", "", 0

    replay_router = ExternalAgentRouter(profiles, executor=replay_execute)
    replay = CachingAgentClient(replay_router, tmp_path)
    assert replay.run_session(
        ("chunk-0", "chunk-1"),
        cache_keys=keys,
        response_validator=validate,
        response_schema="1",
    ) == ("valid", "valid")
    assert replay_calls == [
        ("first", "chunk-0"),
        ("first", "chunk-1"),
        ("second", "chunk-1"),
    ]


def test_session_cache_lands_validated_chunk_even_when_session_later_exhausts(tmp_path):
    """Issue #86 finding: chunk-0 validates and must land on disk the instant
    it does -- if every remaining profile then fails chunk-1 and the whole
    session raises AgentRunError (park path), the already-validated chunk-0
    must not be silently discarded from the cache. This is distinct from
    ``test_session_cache_lands_per_chunk_and_isolates_profiles`` above, which
    only covers the case where the session as a whole eventually succeeds."""
    profiles = (_profile("first", tier=1), _profile("second", tier=2))

    def execute(profile, prompt, attempt):
        if prompt == "chunk-0":
            return "valid", "", 0
        return "bad", "", 0  # every profile fails on chunk-1

    def validate(raw):
        if raw != "valid":
            raise ValueError("invalid")

    keys = ("claude:s1__" + "a" * 64, "claude:s1__" + "b" * 64)
    router = ExternalAgentRouter(profiles, executor=execute)
    cached = CachingAgentClient(router, tmp_path)
    with pytest.raises(Exception, match="fallback exhausted"):
        cached.run_session(
            ("chunk-0", "chunk-1"),
            cache_keys=keys,
            response_validator=validate,
            response_schema="1",
        )

    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    # Only the validated chunk-0 landed; chunk-1 never validated under any
    # profile so nothing was written for it (no partial publication of a
    # chunk that never actually passed validation).
    assert len(payloads) == 1
    assert payloads[0]["output"] == "valid"
    assert payloads[0]["provenance"]["profile_id"] == "first"
