"""PR #162 review finding 2：title 與 skillopt 的預設 router 也要讀寫同一個 health store。

- skillopt 的 rollout router 用 ``task_class="atomization"``、送的是同一份以
  ``---`` 開頭的 atomize prompt，cg 的呼叫格式錯誤在這條路徑同樣必敗。
- title importer 在每個 session 結束的 hook 裡跑，憑證失效這類確定性失敗
  若不退避，每次都會重打外部 CLI。

狀態依 task class 分開記錄：cg 的 ``Invalid command format`` 只在 prompt 以
``-`` 開頭時發生，title prompt（中文開頭）同一個 profile 可能正常；若共用一筆
狀態，title 的成功會清掉 atomization 的退避，兩邊來回翻轉。
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import yaml

from paulsha_hippo import paths
from paulsha_hippo.agent_health import ProfileHealthStore, profile_health_path

_INVOCATION_STDERR = (
    "error: Invalid command format. It looks like your prompt was not quoted, "
    "so the extra words were treated as separate arguments."
)


def _script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _profile_row(profile_id: str, priority: int, script: Path, task_classes: list[str]) -> dict:
    return {
        "id": profile_id,
        "enabled": True,
        "tier": 1,
        "priority": priority,
        "traits": ["test"],
        "task_classes": task_classes,
        "model": "test-model",
        "supported_models": ["test-model"],
        "effort": "medium",
        "supported_efforts": ["medium"],
        "argv": [str(script)],
    }


def _configure_profiles(tmp_path: Path, task_classes: list[str]) -> Path:
    calls = tmp_path / "broken.calls"
    broken = _script(
        tmp_path / "broken.sh",
        "#!/bin/sh\ncat >/dev/null\n"
        f"echo call >> '{calls}'\n"
        f"echo '{_INVOCATION_STDERR}' >&2\n"
        "exit 1\n",
    )
    good = _script(tmp_path / "good.sh", "#!/bin/sh\ncat >/dev/null\nprintf '合成標題'\n")
    config_path = paths.atomizer_config_path()
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["external_agents"]["profiles"] = [
        _profile_row("broken", 1, broken, task_classes),
        _profile_row("good", 2, good, task_classes),
    ]
    config_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return calls


def test_title_router_records_and_honors_backoff(tmp_path):
    from paulsha_hippo.importer import title as title_module

    title = importlib.reload(title_module)  # conftest 把 _default_runner 換成離線版
    memory = tmp_path / "memory"
    calls = _configure_profiles(tmp_path, ["title"])

    session = title.apply(
        {"session_id": "s-1", "user_prompts": ["題目一"], "assistant_summary": "結論一"},
        memory_root=memory,
    )
    assert session["title_source"] == "external-agent"
    assert session["session_title"] == "合成標題"
    assert calls.read_text(encoding="utf-8").count("call") == 1

    store = ProfileHealthStore(profile_health_path(memory))
    entries = store.entries()
    assert entries["title"]["broken"]["kind"] == "invocation"
    assert "atomization" not in entries

    # 下一次 title 生成（另一個 hook 行程）：退避中的 profile 不再被呼叫。
    atom_title, source = title.generate_atom_title("合成筆記內容", memory_root=memory)
    assert (atom_title, source) == ("合成標題", "external-agent")
    assert calls.read_text(encoding="utf-8").count("call") == 1


def test_title_without_memory_root_keeps_previous_behaviour(tmp_path):
    from paulsha_hippo.importer import title as title_module

    title = importlib.reload(title_module)
    calls = _configure_profiles(tmp_path, ["title"])
    out, source = title.generate_title({"user_prompts": ["題目"], "assistant_summary": "結論"})
    assert (out, source) == ("合成標題", "external-agent")
    assert calls.read_text(encoding="utf-8").count("call") == 1
    assert not list(tmp_path.rglob("profile-health.json"))


def _skillopt_args(tmp_path: Path, *, dry_run: bool) -> argparse.Namespace:
    return argparse.Namespace(
        memory_root=str(tmp_path / "memory"),
        reference_root=str(tmp_path / "reference"),
        skill_path=None,
        budget=1,
        dry_run=dry_run,
        now="2026-09-27T00:00:00Z",
    )


def test_skillopt_routers_share_the_health_store(tmp_path, monkeypatch):
    from paulsha_hippo.skillopt import cli as skillopt_cli

    captured: list[dict] = []
    real_router = skillopt_cli.ExternalAgentRouter

    def recording_router(*args, **kwargs):
        captured.append(kwargs)
        return real_router(*args, **kwargs)

    def fake_run_optimize(**kwargs):
        kwargs["make_rollout"]()
        kwargs["make_score"]()
        kwargs["make_optimizer"]()
        return 0

    monkeypatch.setattr(skillopt_cli, "ExternalAgentRouter", recording_router)
    monkeypatch.setattr(skillopt_cli, "run_optimize", fake_run_optimize)

    for dry_run in (False, True):
        captured.clear()
        assert skillopt_cli.run(_skillopt_args(tmp_path, dry_run=dry_run)) == 0
        assert [kwargs["task_class"] for kwargs in captured] == [
            "atomization", "skillopt", "skillopt",
        ]
        for kwargs in captured:
            health = kwargs["health"]
            assert isinstance(health, ProfileHealthStore)
            assert health.path == profile_health_path(tmp_path / "memory")
            assert health.read_only is dry_run


def test_skillopt_rollout_backoff_is_shared_with_atomization(tmp_path, monkeypatch):
    """skillopt rollout 與 dream 都是 atomization task class：同一筆退避互相生效。"""
    from paulsha_hippo.agent_profiles import ExternalAgentRouter
    from paulsha_hippo.atomizer import config as atomizer_config

    calls = _configure_profiles(tmp_path, ["atomization"])
    config, _ = atomizer_config.load_config()
    memory = tmp_path / "memory"
    store = ProfileHealthStore(profile_health_path(memory))
    # dream 那一側先記下 broken 的呼叫格式錯誤……
    ExternalAgentRouter(config.external_profiles, task_class="atomization", health=store).run(
        "---\nname: synthetic\n---\n合成 prompt"
    )
    assert calls.read_text(encoding="utf-8").count("call") == 1
    # ……skillopt rollout 建出的 atomization router 直接略過它。
    from paulsha_hippo.skillopt import cli as skillopt_cli

    captured = {}
    real_router = skillopt_cli.ExternalAgentRouter

    def keep(*args, **kwargs):
        router = real_router(*args, **kwargs)
        captured.setdefault(kwargs["task_class"], router)
        return router

    monkeypatch.setattr(skillopt_cli, "ExternalAgentRouter", keep)
    make_rollout, _score, _optimizer = skillopt_cli._build_default_hooks(
        config,
        skillopt_cli.load_skillopt_config(atomizer_cfg=config),
        memory_root=memory,
        dry_run=False,
    )
    make_rollout()
    rollout_router = captured["atomization"]
    assert rollout_router.run("---\nname: synthetic\n---\n另一個 prompt") == "合成標題"
    assert calls.read_text(encoding="utf-8").count("call") == 1
    assert rollout_router.attempts[0].stderr.startswith("backoff invocation until ")


def test_task_classes_are_tracked_independently(tmp_path):
    from paulsha_hippo.agent_profiles import AgentProfile, AgentRunResult

    profile = AgentProfile.from_mapping(
        {
            "id": "cg", "tier": 1, "priority": 1, "traits": ["test"],
            "task_classes": ["atomization", "title"], "model": "m", "effort": "medium",
            "supported_efforts": ["medium"], "argv": ["true"],
        }
    )

    def result(category, stderr="", exit_code=None):
        return AgentRunResult(
            profile.id, profile.revision, profile.tier, 1, profile.model, profile.effort,
            None, "unverified", profile.command_fingerprint(), 1.0, category, stderr,
            exit_code, None, profile.priority,
        )

    store = ProfileHealthStore(profile_health_path(tmp_path / "memory"), clock=lambda: 1_790_000_000.0)
    store.record_outcome(profile, result("process", _INVOCATION_STDERR, 1), task_class="atomization")
    store.record_outcome(profile, result(None, "", 0), task_class="title")
    assert store.blocked_reason(profile, task_class="atomization") is not None
    assert store.blocked_reason(profile, task_class="title") is None
    assert set(store.entries()) == {"atomization"}
