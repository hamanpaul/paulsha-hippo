# tests/test_push_shadow.py — #158（#148 H1）push shortlist 確定性收窄 shadow 量測。
#
# 驗收重點：
#   - shadow 預設關閉；開啟後注入內容與關閉時逐位元相同，只多寫獨立的 shadow ledger。
#   - 收窄＝BM25 分數門檻（score = -bm25）＋最多 0–3 則，且只從現行注入的 claim 內收窄。
#   - shadow 計算／寫入失敗、超過時間預算都不得影響注入（best-effort）。
#   - 每個 prompt 都不得呼叫外部 LLM（無 subprocess、無網路）。
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from paulsha_hippo import push_shadow as PS
from paulsha_hippo import runtime_flags as rf
from paulsha_hippo.hooks import _shortlist_common as SC
from paulsha_hippo.moc import search as S

REPO = Path(__file__).resolve().parents[1]

_NOTES = (
    # (檔名, slice_id, title, body) — 全為合成內容。
    ("a.md", "sl-aaaaaaaaaaaaaaaa", "SerialWrap", "SerialWrap 抽象 UART 執行層，SerialWrap 為唯一入口\n"),
    ("b.md", "sl-bbbbbbbbbbbbbbbb", "Console 重試", "SerialWrap console 重試策略\n"),
    ("c.md", "sl-cccccccccccccccc", "Log 格式", "UART log 格式約定\n"),
)


def _seed(mr: Path) -> None:
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    for fname, sid, title, body in _NOTES:
        (k / fname).write_text(
            f"---\nmemory_layer: knowledge\nslice_id: {sid}\nproject: proj\n"
            f"title: {title}\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n{body}",
            encoding="utf-8")
    S.build_index(mr, link_weights={})


def _reset_runtime(mr: Path) -> None:
    """清掉 offered／shadow／map 等 runtime 狀態，讓同一 root 可重跑同一序列（索引保留）。"""
    for sub in ("ledger", "wakeup"):
        shutil.rmtree(mr / "runtime" / sub, ignore_errors=True)


def _flags(**shadow) -> rf.HygieneFlags:
    return rf.HygieneFlags(push_shadow=PS.PushShadowConfig(**shadow))


def _shadow_events(mr: Path) -> list[dict]:
    path = PS.ledger_path(mr)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _offered_without_ts(mr: Path) -> list[dict]:
    path = mr / "runtime" / "ledger" / "offered.jsonl"
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for ev in events:
        ev.pop("ts", None)
    return events


_PROMPTS = ("SerialWrap UART 執行", "SerialWrap console", "UART log", "SerialWrap UART 執行")


def _run_sequence(mr: Path, monkeypatch, flags: rf.HygieneFlags, sid: str = "sidS") -> list[str]:
    monkeypatch.setattr(SC, "load_flags", lambda: flags)
    return [SC.build_shortlist_and_record(mr, "claude-code", sid, cwd="/x", prompt=p) for p in _PROMPTS]


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# 設定解析
# ---------------------------------------------------------------------------

def test_config_defaults_disabled():
    cfg = PS.PushShadowConfig()
    assert cfg.enabled is False
    assert cfg.bm25_min_score == PS.DEFAULT_MIN_SCORE == 0.0
    assert cfg.max_k == PS.DEFAULT_MAX_K == 1
    assert 0 < cfg.time_budget_ms == PS.DEFAULT_TIME_BUDGET_MS
    assert rf.HygieneFlags().push_shadow == cfg
    assert rf.load_flags(Path("/nonexistent/config.yaml")).push_shadow == cfg


def test_config_reads_shortlist_push_shadow_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "shortlist:\n  push_shadow:\n    enabled: true\n    bm25_min_score: 2.5\n"
        "    max_k: 2\n    time_budget_ms: 20\n", encoding="utf-8")
    cfg = rf.load_flags(p).push_shadow
    assert cfg == PS.PushShadowConfig(enabled=True, bm25_min_score=2.5, max_k=2, time_budget_ms=20.0)


@pytest.mark.parametrize("raw, expected", [
    ({"enabled": "yes"}, PS.PushShadowConfig()),
    ({"max_k": 4}, PS.PushShadowConfig()),
    ({"max_k": -1}, PS.PushShadowConfig()),
    ({"max_k": True}, PS.PushShadowConfig()),
    ({"max_k": 0}, PS.PushShadowConfig(max_k=0)),
    ({"bm25_min_score": -1}, PS.PushShadowConfig()),
    ({"bm25_min_score": float("nan")}, PS.PushShadowConfig()),
    ({"bm25_min_score": "3"}, PS.PushShadowConfig()),
    ({"bm25_min_score": 3}, PS.PushShadowConfig(bm25_min_score=3.0)),
    ({"time_budget_ms": 0}, PS.PushShadowConfig()),
    ({"time_budget_ms": 5000}, PS.PushShadowConfig()),
    ("not-a-mapping", PS.PushShadowConfig()),
    (None, PS.PushShadowConfig()),
])
def test_config_invalid_values_fall_back_per_key(raw, expected):
    assert PS.parse_config(raw) == expected


def test_template_declares_push_shadow_disabled():
    import yaml
    tpl = REPO / "paulsha_hippo" / "atomizer" / "atomizer.yaml"
    data = yaml.safe_load(tpl.read_text(encoding="utf-8"))
    shadow = data["shortlist"]["push_shadow"]
    assert shadow["enabled"] is False
    assert PS.parse_config(shadow) == PS.PushShadowConfig()


# ---------------------------------------------------------------------------
# 純函式：收窄
# ---------------------------------------------------------------------------

def test_narrowed_indices_threshold_and_cap_preserve_rank_order():
    bm25 = [-6.0, -4.0, -1.5]
    assert PS.narrowed_indices(bm25, min_score=0.0, max_k=3) == [0, 1, 2]
    assert PS.narrowed_indices(bm25, min_score=4.0, max_k=3) == [0, 1]
    assert PS.narrowed_indices(bm25, min_score=4.0, max_k=1) == [0]
    assert PS.narrowed_indices(bm25, min_score=10.0, max_k=3) == []
    assert PS.narrowed_indices(bm25, min_score=0.0, max_k=0) == []
    # 排序不保證 bm25 單調（link_weight／usage boost）：門檻逐筆判斷、保留原順序
    assert PS.narrowed_indices([-1.0, -5.0, -3.0], min_score=2.0, max_k=3) == [1, 2]
    # 分數缺漏／非數值一律不收
    assert PS.narrowed_indices([None, "x", -5.0], min_score=0.0, max_k=3) == [2]


# ---------------------------------------------------------------------------
# build_shortlist_and_record 整合
# ---------------------------------------------------------------------------

def test_shadow_disabled_by_default_writes_no_shadow_ledger(seeded, monkeypatch):
    out = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    assert out[0] != ""
    assert not PS.ledger_path(seeded).exists()


def test_shadow_enabled_injection_is_byte_identical_and_only_adds_shadow_ledger(seeded, monkeypatch):
    off = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    off_offered = _offered_without_ts(seeded)
    off_map = (seeded / "runtime" / "wakeup" / "claude-code__sidS.offered.json").read_bytes()
    _reset_runtime(seeded)

    on = _run_sequence(seeded, monkeypatch, _flags(enabled=True, bm25_min_score=0.0, max_k=1))

    assert [s.encode("utf-8") for s in on] == [s.encode("utf-8") for s in off]
    assert any(on)
    assert _offered_without_ts(seeded) == off_offered
    assert (seeded / "runtime" / "wakeup" / "claude-code__sidS.offered.json").read_bytes() == off_map
    ledger_files = sorted(p.name for p in (seeded / "runtime" / "ledger").iterdir())
    assert ledger_files == ["offered.jsonl", PS.LEDGER_NAME]
    # 每次「有注入」恰一筆 shadow 事件；空注入（全已 offer）不記
    assert len(_shadow_events(seeded)) == sum(1 for s in on if s)


def test_shadow_event_records_before_after_ids_and_chars(seeded, monkeypatch):
    out = _run_sequence(seeded, monkeypatch, _flags(enabled=True, bm25_min_score=0.0, max_k=1))
    events = _shadow_events(seeded)
    first = events[0]
    offered = json.loads((seeded / "runtime" / "ledger" / "offered.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    before_ids = [b["sl_id"] for b in first["before"]]
    assert before_ids == [o["sl_id"] for o in offered["offered"]]
    assert first["after"] == before_ids[:1]
    assert first["dropped"] == before_ids[1:]
    assert first["chars_before"] == len(out[0])
    assert first["chars_exact"] is True
    assert 0 < first["chars_after"] < first["chars_before"]
    assert first["schema"] == PS.SCHEMA_VERSION
    assert first["tool"] == "claude-code" and first["session_id"] == "sidS"
    assert first["project"] == "proj"
    assert first["params"] == {"bm25_min_score": 0.0, "max_k": 1}
    assert first["candidates"] >= len(before_ids)
    for b in first["before"]:
        assert isinstance(b["bm25"], float) and b["bm25"] < 0
        assert isinstance(b["rank"], int) and b["rank"] >= 0
    assert len(first["query_sha256"]) == 64 and first["query_terms"] >= 1
    assert "query" not in first and "prompt" not in first  # 不落 prompt／query 原文
    assert first["pipeline_ms"] >= 0 and first["shadow_ms"] >= 0


def test_shadow_chars_after_equals_real_injection_of_narrowed_selection(seeded, monkeypatch):
    """chars_after 必須等於「現行管線只注入 after 那幾則」時的實際字元數。"""
    _run_sequence(seeded, monkeypatch, _flags(enabled=True, bm25_min_score=0.0, max_k=1))
    first = _shadow_events(seeded)[0]
    _reset_runtime(seeded)
    monkeypatch.setattr(SC, "SHORTLIST_K", 1)
    monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags())
    only_top1 = SC.build_shortlist_and_record(seeded, "claude-code", "sidS", cwd="/x", prompt=_PROMPTS[0])
    assert first["chars_after"] == len(only_top1)


def test_shadow_threshold_can_drop_everything(seeded, monkeypatch):
    out = _run_sequence(seeded, monkeypatch, _flags(enabled=True, bm25_min_score=1e9, max_k=3))
    assert out[0] != ""
    first = _shadow_events(seeded)[0]
    assert first["after"] == [] and first["chars_after"] == 0
    assert first["dropped"] == [b["sl_id"] for b in first["before"]]


def test_shadow_max_k_three_with_zero_threshold_is_identity(seeded, monkeypatch):
    out = _run_sequence(seeded, monkeypatch, _flags(enabled=True, bm25_min_score=0.0, max_k=3))
    first = _shadow_events(seeded)[0]
    assert first["after"] == [b["sl_id"] for b in first["before"]]
    assert first["dropped"] == []
    assert first["chars_after"] == first["chars_before"] == len(out[0])


def test_shadow_failure_never_affects_injection(seeded, monkeypatch):
    off = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    _reset_runtime(seeded)

    def _boom(*a, **k):
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(PS, "build_event", _boom)
    on = _run_sequence(seeded, monkeypatch, _flags(enabled=True))
    assert on == off
    assert not PS.ledger_path(seeded).exists()
    assert (seeded / "runtime" / "ledger" / "offered.jsonl").exists()


def test_shadow_record_raising_is_contained_at_call_site(seeded, monkeypatch):
    off = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    _reset_runtime(seeded)

    def _boom(*a, **k):
        raise RuntimeError("record exploded outside its own guard")

    monkeypatch.setattr(PS, "record", _boom)
    assert _run_sequence(seeded, monkeypatch, _flags(enabled=True)) == off


def test_shadow_ledger_write_failure_never_affects_injection(seeded, monkeypatch):
    off = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    _reset_runtime(seeded)
    blocker = PS.ledger_path(seeded)
    blocker.mkdir(parents=True)  # 同名目錄 → os.open 失敗
    on = _run_sequence(seeded, monkeypatch, _flags(enabled=True))
    assert on == off
    assert blocker.is_dir()


def test_shadow_over_time_budget_drops_record_but_keeps_injection(seeded, monkeypatch):
    off = _run_sequence(seeded, monkeypatch, rf.HygieneFlags())
    _reset_runtime(seeded)
    ticks = iter(float(i) for i in range(10_000))  # 每次讀時鐘前進 1 秒
    monkeypatch.setattr(PS, "clock", lambda: next(ticks))
    on = _run_sequence(seeded, monkeypatch, _flags(enabled=True, time_budget_ms=50))
    assert on == off
    assert not PS.ledger_path(seeded).exists()
    log = (seeded / "log" / "hooks.log").read_text(encoding="utf-8")
    assert "push shadow" in log and "budget" in log


def test_shadow_never_spawns_process_or_opens_network(seeded, monkeypatch):
    """每個 prompt 都不得呼叫外部 LLM：shadow 開啟下整條管線不得 spawn 子行程或開 socket。"""
    def _forbidden(*a, **k):
        raise AssertionError("prompt-time path must not call external processes / network")

    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    out = _run_sequence(seeded, monkeypatch, _flags(enabled=True))
    assert out[0] != ""
    assert len(_shadow_events(seeded)) >= 1


def test_shadow_does_not_rerun_search(seeded, monkeypatch):
    calls = []
    real = S.search

    def _counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(SC.search_mod, "search", _counting)
    _run_sequence(seeded, monkeypatch, _flags(enabled=True))
    assert len(calls) == len(_PROMPTS)  # shadow 沿用同一次檢索結果，不另查


def test_explicit_recall_path_records_no_push_shadow(seeded, monkeypatch):
    monkeypatch.setattr(SC, "load_flags", lambda: _flags(enabled=True))
    out = SC.build_shortlist_and_record(seeded, "claude-code", "sidR", cwd="/x", prompt=_PROMPTS[0],
                                        bypass_early_stop=True, record_push_shadow=False)
    assert out != ""
    assert not PS.ledger_path(seeded).exists()


def test_cli_recall_does_not_record_push_shadow(seeded, monkeypatch, capsys):
    from paulsha_hippo import cli
    monkeypatch.setattr(SC, "load_flags", lambda: _flags(enabled=True))
    rc = cli.main(["recall", "--memory-root", str(seeded), "--cwd", "/x", "--prompt", _PROMPTS[0],
                   "--tool", "claude-code", "--session-id", "sidC"])
    assert rc == 0 and capsys.readouterr().out.strip() != ""
    assert not PS.ledger_path(seeded).exists()


def test_no_injection_means_no_shadow_event(seeded, monkeypatch):
    monkeypatch.setattr(SC, "load_flags", lambda: _flags(enabled=True))
    assert SC.build_shortlist_and_record(seeded, "claude-code", "sidN", cwd="/x", prompt="zzzznomatch") == ""
    assert SC.build_shortlist_and_record(seeded, "claude-code", "sidN", cwd="/x", prompt="/slash") == ""
    assert not PS.ledger_path(seeded).exists()


# ---------------------------------------------------------------------------
# 真實 hook 進程（claude／copilot）：stdout 逐位元相同
# ---------------------------------------------------------------------------

def _hook_env(tmp_path: Path, mr: Path, shadow_enabled: bool) -> dict:
    cfg_root = tmp_path / f"cfg-{'on' if shadow_enabled else 'off'}"
    cfg_root.mkdir(exist_ok=True)
    text = (REPO / "paulsha_hippo" / "atomizer" / "atomizer.yaml").read_text(encoding="utf-8")
    if shadow_enabled:
        text = text.replace("    enabled: false   # #158", "    enabled: true    # #158")
        assert "enabled: true    # #158" in text
    (cfg_root / "config.yaml").write_text(text, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PSC_MEMORY_ROOT": str(mr), "HIPPO_CONFIG_ROOT": str(cfg_root), "HOME": str(home),
            "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)}


@pytest.mark.parametrize("hook, payload_key", [
    ("claude_user_prompt_submit.py", "session_id"),
    ("copilot_user_prompt_submit.py", "sessionId"),
])
def test_hook_process_stdout_byte_identical_with_shadow_on(tmp_path, hook, payload_key):
    mr = tmp_path / "mr"
    _seed(mr)
    proj_cwd = mr / "proj"
    proj_cwd.mkdir(exist_ok=True)
    script = REPO / "paulsha_hippo" / "hooks" / hook
    payload = json.dumps({payload_key: "hk1", "cwd": str(proj_cwd), "prompt": _PROMPTS[0]})

    def _run(enabled: bool) -> bytes:
        p = subprocess.run([sys.executable, str(script)], input=payload.encode("utf-8"),
                           capture_output=True, env=_hook_env(tmp_path, mr, enabled))
        assert p.returncode == 0, p.stderr
        return p.stdout

    off = _run(False)
    assert not PS.ledger_path(mr).exists()
    _reset_runtime(mr)
    on = _run(True)
    assert on == off
    assert b"sl-aaaaaaaaaaaaaaaa" in on
    events = _shadow_events(mr)
    assert len(events) == 1 and events[0]["session_id"] == "hk1"
