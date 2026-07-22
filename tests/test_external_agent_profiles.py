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


def test_cache_identity_is_profile_specific():
    first = _profile("first")
    second = _profile("second")
    common = {"operation": "atomize", "config_hash": "c", "skill_hash": "s", "prompt_hash": "p"}
    assert cache_identity(profile=first, **common) != cache_identity(profile=second, **common)


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
