"""issue #157 現象 1：已知必敗的 profile 要進入可見的持久退避狀態。

0.1.1 與 main 的 router 只有 60 秒的 in-memory circuit，且不跨 process；
每小時一次的 dream run 都會重建 router，所以 cg 這種「呼叫格式錯誤、每次都
1.9 秒 exit 1」的 profile 每一輪都先被試一次。這裡驗證：

- 確定性失敗（CLI 呼叫格式錯誤、憑證失效）寫入 memory root 下的持久 health
  狀態並進入指數退避；後續 session（包括新 router／新 process）不再先試它，
  而是留下帶原因的略過紀錄。
- 非確定性失敗（timeout、invalid_output、一般 exit 1、quota）不進退避。
- 成功或非確定性結果清除狀態；command 變更後舊狀態不再套用。
- ``hippo doctor`` 顯示退避狀態。
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from paulsha_hippo import agent_health, ops
from paulsha_hippo.agent_profiles import (
    AgentProfile,
    AgentRunError,
    AgentRunResult,
    ExternalAgentRouter,
)

_INVOCATION_STDERR = (
    "error: Invalid command format. It looks like your prompt was not quoted, "
    "so the extra words were treated as separate arguments."
)


def _profile(profile_id: str, *, priority: int = 1, argv=None) -> AgentProfile:
    return AgentProfile.from_mapping(
        {
            "id": profile_id,
            "tier": 1,
            "priority": priority,
            "traits": ["test"],
            "task_classes": ["atomization"],
            "model": "test-model",
            "effort": "medium",
            "supported_efforts": ["medium"],
            "argv": list(argv or ["true"]),
        }
    )


class _Clock:
    def __init__(self, now: float = 1_790_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _result(profile: AgentProfile, *, category, stderr="", exit_code=None, elapsed=1.0):
    return AgentRunResult(
        profile.id, profile.revision, profile.tier, 1, profile.model, profile.effort,
        None, "unavailable" if category else "unverified", profile.command_fingerprint(),
        elapsed, category, stderr, exit_code, None, profile.priority,
    )


def _store(tmp_path: Path, clock: _Clock, **kwargs) -> agent_health.ProfileHealthStore:
    return agent_health.ProfileHealthStore(
        agent_health.profile_health_path(tmp_path / "memory"), clock=clock, **kwargs
    )


def _chain():
    return (_profile("cg", priority=5), _profile("codex", priority=20))


def _cg_invocation_error(calls):
    def execute(profile, prompt, attempt):  # noqa: ARG001
        calls.append(profile.id)
        if profile.id == "cg":
            raise AgentRunError(
                "agent exited with code 1", category="process", profile_id="cg",
                exit_code=1, stderr=_INVOCATION_STDERR,
            )
        return "answer", "", 0

    return execute


def test_invocation_error_backs_off_across_router_instances(tmp_path):
    clock = _Clock()
    calls: list[str] = []
    first = ExternalAgentRouter(
        _chain(), executor=_cg_invocation_error(calls), health=_store(tmp_path, clock)
    )
    assert first.run("session-1") == "answer"
    assert calls == ["cg", "codex"]

    state_path = agent_health.profile_health_path(tmp_path / "memory")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["task_classes"]["atomization"]["cg"]
    assert entry["kind"] == "invocation"
    assert entry["consecutive_failures"] == 1
    assert entry["blocked_until"] == clock.now + agent_health.BACKOFF_BASE_SECONDS
    assert "Invalid command format" in entry["last_stderr"]

    # 下一個 dream run：新 process、新 router、in-memory circuit 已清空。
    clock.now += 120
    calls.clear()
    second = ExternalAgentRouter(
        _chain(), executor=_cg_invocation_error(calls), health=_store(tmp_path, clock)
    )
    assert second.run("session-2") == "answer"
    assert calls == ["codex"]
    skipped = second.attempts[0]
    assert skipped.profile_id == "cg"
    assert skipped.failure_category == "ineligible"
    assert skipped.elapsed_seconds == 0.0
    assert skipped.stderr.startswith("backoff invocation until ")
    assert second.attempts[1].profile_id == "codex"
    assert second.attempts[1].failure_category is None


def test_backoff_expiry_probes_once_and_doubles_on_repeat(tmp_path):
    clock = _Clock()
    calls: list[str] = []
    store = _store(tmp_path, clock)
    ExternalAgentRouter(_chain(), executor=_cg_invocation_error(calls), health=store).run("s1")

    clock.now += agent_health.BACKOFF_BASE_SECONDS + 1
    calls.clear()
    ExternalAgentRouter(_chain(), executor=_cg_invocation_error(calls), health=store).run("s2")
    assert calls == ["cg", "codex"]  # 退避到期後允許一次探測
    entry = _entry(store, "cg")
    assert entry["consecutive_failures"] == 2
    assert entry["blocked_until"] == clock.now + 2 * agent_health.BACKOFF_BASE_SECONDS


def test_backoff_is_capped(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock)
    profile = _profile("cg")
    for _ in range(20):
        store.record_outcome(
            profile, _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1)
        )
    entry = _entry(store, "cg")
    assert entry["blocked_until"] - clock.now == agent_health.BACKOFF_CAP_SECONDS


def test_success_clears_backoff_state(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock)
    profile = _profile("cg")
    store.record_outcome(
        profile, _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1)
    )
    assert store.blocked_reason(profile) is not None
    store.record_outcome(profile, _result(profile, category=None, exit_code=0))
    assert store.entries() == {}
    assert store.blocked_reason(profile) is None


@pytest.mark.parametrize(
    ("category", "stderr", "exit_code"),
    [
        ("timeout", "agent timed out after 600s", None),
        ("invalid_output", "agent output must be one JSON value without surrounding noise", 0),
        ("process", "agent exited with code 1", 1),
        ("quota", "rate limit exceeded", 1),
        # codex 會把整段 prompt 回顯到 stderr：出現在 banner 之後的字樣不得觸發退避。
        (
            "process",
            "OpenAI Codex v0.157.1 -------- workdir: /tmp/x model: m -------- user "
            "error: unknown option --foo appears inside the echoed session text",
            1,
        ),
    ],
)
def test_non_deterministic_failures_do_not_back_off(tmp_path, category, stderr, exit_code):
    clock = _Clock()
    store = _store(tmp_path, clock)
    profile = _profile("claude")
    store.record_outcome(profile, _result(profile, category=category, stderr=stderr, exit_code=exit_code))
    assert store.blocked_reason(profile) is None
    assert "claude" not in store.entries().get("atomization", {})


def test_slow_failure_with_invocation_text_is_not_deterministic(tmp_path):
    """真正的參數解析錯誤是啟動即失敗；跑了很久才出現的相同字樣不算。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    profile = _profile("cg")
    store.record_outcome(
        profile,
        _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1, elapsed=240.0),
    )
    assert store.blocked_reason(profile) is None


def test_credential_failure_needs_two_consecutive_sessions(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock)
    profile = _profile("cg")
    auth = _result(
        profile, category="auth", stderr="Error: No authentication information found.", exit_code=1
    )
    store.record_outcome(profile, auth)
    assert store.blocked_reason(profile) is None
    assert _entry(store, "cg")["consecutive_failures"] == 1
    store.record_outcome(profile, auth)
    reason = store.blocked_reason(profile)
    assert reason is not None and reason.startswith("backoff credential until ")


def test_command_change_invalidates_backoff(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock)
    old = _profile("cg", argv=["cg", "--old"])
    store.record_outcome(old, _result(old, category="process", stderr=_INVOCATION_STDERR, exit_code=1))
    assert store.blocked_reason(old) is not None
    fixed = _profile("cg", argv=["cg", "--fixed"])
    assert store.blocked_reason(fixed) is None


def test_corrupt_state_file_is_treated_as_empty(tmp_path):
    clock = _Clock()
    path = agent_health.profile_health_path(tmp_path / "memory")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    store = agent_health.ProfileHealthStore(path, clock=clock)
    assert store.blocked_reason(_profile("cg")) is None
    assert store.entries() == {}


def test_read_only_store_never_writes(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock, read_only=True)
    profile = _profile("cg")
    store.record_outcome(profile, _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1))
    assert not agent_health.profile_health_path(tmp_path / "memory").exists()


def test_doctor_shows_backoff_state(tmp_path, monkeypatch):
    """doctor 必須看得到哪個 profile 在退避、原因與到期時間（不改 exit code 語意）。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    memory = tmp_path / "memory"
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(memory))
    config, _ = atomizer_config.load_config()
    codex = next(profile for profile in config.external_profiles if profile.id == "codex")
    clock = _Clock()
    store = agent_health.ProfileHealthStore(agent_health.profile_health_path(memory), clock=clock)
    store.record_outcome(
        codex, _result(codex, category="process", stderr=_INVOCATION_STDERR, exit_code=1)
    )

    with mock.patch.object(agent_health.time, "time", return_value=clock.now + 60):
        lines, failed = ops._probe_external_profiles(live=False)
    codex_line = next(line for line in lines if " id=codex " in line)
    assert "health[atomization]=backoff(invocation)" in codex_line
    assert "Invalid command format" in codex_line
    claude_line = next(line for line in lines if " id=claude " in line)
    assert "health[" not in claude_line
    assert failed is False


# --- review finding 1：並行寫入 -------------------------------------------------


def _entry(store, profile_id, task_class="atomization"):
    return store.entries()[task_class][profile_id]


def _hammer(path_str, start_event, rounds):
    store = agent_health.ProfileHealthStore(path_str, clock=lambda: 1_790_000_000.0)
    profile = _profile("cg")
    result = _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1)
    start_event.wait()
    for _ in range(rounds):
        store.record_outcome(profile, result)


def test_concurrent_writers_do_not_lose_updates(tmp_path):
    """兩個以上 atomize／dream／importer 行程同時更新時，退避計數不得倒退或消失。"""
    import multiprocessing

    path = agent_health.profile_health_path(tmp_path / "memory")
    ctx = multiprocessing.get_context("fork")
    start = ctx.Event()
    workers = [ctx.Process(target=_hammer, args=(str(path), start, 30)) for _ in range(4)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(120)
        assert worker.exitcode == 0
    store = agent_health.ProfileHealthStore(path, clock=lambda: 1_790_000_000.0)
    assert _entry(store, "cg")["consecutive_failures"] == 4 * 30
    assert list(path.parent.glob("*.tmp")) == []


def test_save_does_not_depend_on_a_fixed_temp_name(tmp_path):
    """固定暫存檔名會讓交錯的 writer 互刪對方的暫存檔；佔住舊的固定名稱也必須寫得進去。"""
    path = agent_health.profile_health_path(tmp_path / "memory")
    path.parent.mkdir(parents=True)
    (path.parent / f".{path.name}.tmp").mkdir()
    store = agent_health.ProfileHealthStore(path, clock=_Clock())
    profile = _profile("cg")
    store.record_outcome(
        profile, _result(profile, category="process", stderr=_INVOCATION_STDERR, exit_code=1)
    )
    assert _entry(store, "cg")["consecutive_failures"] == 1
