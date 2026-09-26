"""issue #157 現象 3：全部 profile 被判不可用時，失敗紀錄必須帶每個 profile 的略過原因。

生產形狀（0.1.1 與 main 相同）：一次 dream／atomize run 內所有 session 共用同一個
``ExternalAgentRouter``；任何真實失敗都會替該 profile 開 60 秒的 in-memory
circuit。第一個 session 在幾秒內把整條鏈打穿（cg invocation error、claude
exit 1、codex exit 1）之後，接下來 60 秒內的每個 session 都發現所有 circuit
皆開、被主迴圈「靜默 continue」——沒有任何 attempt 紀錄，`_raise_exhausted`
拿到 ``last=None``，最後以 ``backend_unavailable``、``attempts=0``、空
``attempts_detail`` 被 park。2026-09-26 失敗佇列最近 40 筆中 36 筆就是這個形狀。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from paulsha_hippo.agent_profiles import (
    AgentProfile,
    AgentRunError,
    ExternalAgentRouter,
)
from paulsha_hippo.atomizer import agent_exec, config as atomizer_config, llm_promoter, pipeline
from paulsha_hippo.ledger import processing


def _profile(profile_id: str, *, tier: int = 1, priority: int = 1, argv=None) -> AgentProfile:
    return AgentProfile.from_mapping(
        {
            "id": profile_id,
            "tier": tier,
            "priority": priority,
            "traits": ["test"],
            "task_classes": ["atomization"],
            "model": "test-model",
            "effort": "medium",
            "supported_efforts": ["medium"],
            "argv": list(argv or ["true"]),
        }
    )


def _chain():
    return (
        _profile("cg", tier=1, priority=5),
        _profile("claude", tier=1, priority=10),
        _profile("codex", tier=1, priority=20),
    )


def _fail_fast(profile, prompt, attempt):  # noqa: ARG001
    raise AgentRunError(
        f"{profile.id} failed", category="process", profile_id=profile.id,
        exit_code=1, stderr=f"{profile.id} exit 1",
    )


def test_circuit_open_profiles_leave_skip_records_on_following_session():
    router = ExternalAgentRouter(_chain(), executor=_fail_fast)
    with pytest.raises(AgentRunError):
        router.run("session-1")
    assert [a.profile_id for a in router.attempts] == ["cg", "claude", "codex"]

    calls: list[str] = []

    def must_not_run(profile, prompt, attempt):  # noqa: ARG001
        calls.append(profile.id)
        return "unexpected", "", 0

    router._executor = must_not_run
    with pytest.raises(AgentRunError) as excinfo:
        router.run("session-2")

    # 被 circuit 擋下的 profile 不得被呼叫……
    assert calls == []
    # ……但每一個都必須留下略過原因，而不是空的 attempts。
    assert [a.profile_id for a in router.attempts] == ["cg", "claude", "codex"]
    for attempt in router.attempts:
        assert attempt.failure_category == "ineligible"
        assert attempt.elapsed_seconds == 0.0
        assert attempt.stderr.startswith("circuit_open")
        assert "process" in attempt.stderr
    # 全部被略過時 raise 的分類維持 ineligible（→ backend_unavailable），
    # 但錯誤訊息要帶每個 profile 的原因。
    assert excinfo.value.category == "ineligible"
    message = str(excinfo.value)
    for profile_id in ("cg", "claude", "codex"):
        assert f"{profile_id} circuit_open" in message


def test_circuit_skip_records_do_not_consume_attempt_budget():
    """circuit 略過紀錄是 provenance，不得吃掉 max_attempts，否則仍健康的 profile 會被擠掉。"""
    profiles = (_profile("a", priority=1), _profile("b", priority=2), _profile("c", priority=3))
    calls: list[str] = []

    def execute(profile, prompt, attempt):  # noqa: ARG001
        calls.append(profile.id)
        return "answer", "", 0

    router = ExternalAgentRouter(profiles, executor=execute, max_attempts=1)
    far_future = time.monotonic() + 3600.0
    router._circuit_open_until["a"] = far_future
    router._circuit_open_until["b"] = far_future

    assert router.run("prompt") == "answer"
    assert calls == ["c"]
    assert [a.profile_id for a in router.attempts] == ["a", "b", "c"]
    assert [a.failure_category for a in router.attempts] == ["ineligible", "ineligible", None]


def test_circuit_skip_after_real_failure_keeps_terminal_real_attempt():
    """真實失敗之後的 circuit 略過紀錄只是 provenance：raise 仍回報最後一個真跑的 attempt，
    park 分類不因新增紀錄而改變（比照 #106 的 skip record 契約）。"""
    profiles = (_profile("a", priority=1), _profile("b", priority=2))

    def execute(profile, prompt, attempt):  # noqa: ARG001
        raise AgentRunError("a timeout", category="timeout", profile_id=profile.id)

    router = ExternalAgentRouter(profiles, executor=execute)
    router._circuit_open_until["b"] = time.monotonic() + 3600.0
    with pytest.raises(AgentRunError) as excinfo:
        router.run("prompt")
    assert [a.profile_id for a in router.attempts] == ["a", "b"]
    assert router.attempts[1].stderr.startswith("circuit_open")
    assert excinfo.value.category == "timeout"
    assert excinfo.value.profile_id == "a"


_RAW_TEMPLATE = """---
memory_layer: inbox
project: demo
source_agent: claude
source_session: {session}
source_artifact: research
captured_at: "2026-09-26T00:00:00Z"
provenance:
  repo: demo
  commit: c
  path: docs/x.md
---
# Topic A
alpha body {session}
"""


def _seed_raw(root: Path, session: str) -> None:
    raw = root / "inbox" / "research" / "claude" / "2026-09-26" / f"{session}.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW_TEMPLATE.format(session=session), encoding="utf-8")


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_backend_unavailable_park_evidence_names_every_skipped_profile(tmp_path):
    """同一 run 的第二個 session 在所有 circuit 皆開時被 park：attempts_detail 不得為空。"""
    root = tmp_path / "memory"
    _seed_raw(root, "s1")
    _seed_raw(root, "s2")
    failing = _write_script(
        tmp_path / "fake-fail.sh",
        "#!/bin/sh\ncat >/dev/null\necho 'synthetic failure' >&2\nexit 1\n",
    )
    profiles = (
        _profile("cg", tier=1, priority=5, argv=[str(failing)]),
        _profile("claude", tier=1, priority=10, argv=[str(failing)]),
    )
    cfg, config_hash = atomizer_config.load_config(override_path=None)
    router = ExternalAgentRouter(profiles)
    cached = agent_exec.CachingAgentClient(router, root / "runtime" / "cache" / "atomize")
    promoter = llm_promoter.LLMPromoter(
        cached, skill_text="SKIP-REASON-SKILL", known_projects=["demo"], model="fake",
    )
    pipeline.run(
        root, config=cfg, config_hash=config_hash,
        now="2026-09-26T16:00:52Z", promoter=promoter,
    )

    evidence = {}
    for session in ("s1", "s2"):
        assert processing.state_of(root, f"claude:{session}") == "parked"
        path = root / "runtime" / "queue" / "_failed" / f"claude__{session}.json"
        evidence[session] = json.loads(path.read_text(encoding="utf-8"))

    # 真的跑過的那個 session 帶真實失敗證據；另一個 session 在 circuit 全開時被略過。
    real = [
        item for item in evidence.values()
        if any(d["failure_kind"] == "nonzero_exit" for d in item["attempts_detail"])
    ]
    skipped = [item for item in evidence.values() if item not in real]
    assert len(real) == 1 and len(skipped) == 1
    skipped_payload = skipped[0]
    assert skipped_payload["failure_category"] == "backend_unavailable"
    detail = skipped_payload["attempts_detail"]
    assert [d["profile_id"] for d in detail] == ["cg", "claude"]
    for item in detail:
        assert item["failure_kind"] == "ineligible"
        assert item["stderr_tail"].startswith("circuit_open")
    assert "cg circuit_open" in skipped_payload["error"]
    assert "claude circuit_open" in skipped_payload["error"]
