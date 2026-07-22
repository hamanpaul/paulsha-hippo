from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import pytest

from paulsha_hippo.agent_profiles import (
    AgentProfile,
    ExternalAgentRouter,
    ProfileConfigError,
    cache_identity,
    child_environment,
    default_profiles,
)
from paulsha_hippo.atomizer.agent_exec import CachingAgentClient

STUB = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"


def _profile(profile_id: str, *, tier: int = 1, priority: int = 1, argv: tuple[str, ...] | None = None) -> AgentProfile:
    return AgentProfile.from_mapping(
        {
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
    )


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


def test_router_session_validates_each_chunk_and_restarts_from_frozen_chunk_zero():
    from paulsha_hippo.agent_profiles import AgentRunError

    profiles = (_profile("first", tier=1), _profile("second", tier=2))
    calls: list[tuple[str, str, int]] = []

    def execute(profile, prompt, call):
        calls.append((profile.id, prompt, call))
        if profile.id == "first" and prompt == "chunk-1":
            return "malformed", "", 0
        return "valid", "", 0

    def validate(raw):
        if raw != "valid":
            raise ValueError("response schema mismatch")

    router = ExternalAgentRouter(profiles, executor=execute)
    assert router.run_session(("chunk-0", "chunk-1"), response_validator=validate) == (
        "valid",
        "valid",
    )
    assert calls == [
        ("first", "chunk-0", 1),
        ("first", "chunk-1", 2),
        ("second", "chunk-0", 3),
        ("second", "chunk-1", 4),
    ]
    assert router.last_result is not None
    assert router.last_result.profile_id == "second"
    assert router.last_result.fallback_reason == "degraded-success"
    assert router.attempts[0].failure_category == "invalid_output"


def test_router_session_shares_call_budget_across_fallback_restarts():
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
        router.run_session(("chunk-0", "chunk-1"), response_validator=validate)
    assert calls == [("first", "chunk-0"), ("first", "chunk-1"), ("second", "chunk-0")]
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


def test_session_cache_never_mixes_profiles_or_persists_partial_chunks(tmp_path):
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
    assert calls == [
        ("first", "chunk-0"),
        ("first", "chunk-1"),
        ("second", "chunk-0"),
        ("second", "chunk-1"),
    ]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert len(payloads) == 2
    assert {payload["provenance"]["profile_id"] for payload in payloads} == {"second"}
    assert all(payload["response_schema"] == "1" for payload in payloads)

    def should_not_execute(profile, prompt, attempt):
        raise AssertionError("complete selected-profile cache must be reusable")

    replay_router = ExternalAgentRouter(profiles, executor=should_not_execute)
    replay = CachingAgentClient(replay_router, tmp_path)
    assert replay.run_session(
        ("changed prompt is deliberately ignored by explicit cache keys", "chunk-1"),
        cache_keys=keys,
        response_validator=validate,
        response_schema="1",
    ) == ("valid", "valid")
