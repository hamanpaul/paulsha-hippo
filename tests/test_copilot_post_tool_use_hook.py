# tests/test_copilot_post_tool_use_hook.py —（條件 task：matrix verdict=supported 才存在）
# copilot postToolUse(view) read attribution adapter（Task 6 Step 6 對稱面）。
# 實測 payload 形（payload_probe 2026-07-11）：
#   {"sessionId": ..., "cwd": ..., "toolName": "view",
#    "toolArgs": "{\"path\": \"...\", \"view_range\": [1, 5]}", "toolResult": {...}}
# 注意 toolArgs 是 JSON「字串」，不是物件。
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/copilot_post_tool_use.py").resolve()


def _map(mr: Path, sid: str, path: str, slid: str):
    wk = mr / "runtime" / "wakeup"; wk.mkdir(parents=True, exist_ok=True)
    (wk / f"copilot-cli__{sid}.offered.json").write_text(
        json.dumps({"by_path": {path: slid}, "by_id": {slid: path}}), encoding="utf-8")


def _run(mr: Path, payload: dict):
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr


def _events(mr: Path):
    f = mr / "runtime" / "ledger" / "memory_usage.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def _view_payload(sid: str, path: str) -> dict:
    return {"sessionId": sid, "timestamp": 0, "cwd": "/x", "toolName": "view",
            "toolArgs": json.dumps({"path": path, "view_range": [1, 5]}),
            "toolResult": {"resultType": "success", "textResultForLlm": "1. x"}}


def test_view_offered_knowledge_path_records_used(tmp_path):
    note = tmp_path / "knowledge" / "proj" / "a.md"; note.parent.mkdir(parents=True)
    note.write_text("---\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n---\nx\n", encoding="utf-8")
    _map(tmp_path, "s1", str(note), "sl-aaaaaaaaaaaaaaaa")
    _run(tmp_path, _view_payload("s1", str(note)))
    ev = _events(tmp_path)
    assert len(ev) == 1 and ev[0]["source"] == "read" and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa" and ev[0]["tool"] == "copilot-cli"


def test_view_non_offered_knowledge_records_offered_false(tmp_path):
    note = tmp_path / "knowledge" / "proj" / "b.md"; note.parent.mkdir(parents=True)
    note.write_text("---\nslice_id: sl-bbbbbbbbbbbbbbbb\n---\nx\n", encoding="utf-8")
    _run(tmp_path, _view_payload("s9", str(note)))
    ev = _events(tmp_path)
    assert len(ev) == 1 and ev[0]["offered"] is False and ev[0]["sl_id"] == "sl-bbbbbbbbbbbbbbbb"


def test_view_non_knowledge_path_no_event(tmp_path):
    other = tmp_path / "elsewhere.md"; other.write_text("hi", encoding="utf-8")
    _run(tmp_path, _view_payload("s1", str(other)))
    assert _events(tmp_path) == []


def test_view_of_knowledge_directory_no_event(tmp_path):
    # 實測：copilot 的 view 可列目錄；目錄不構成 slice read，不得記空 attribution 事件
    kdir = tmp_path / "knowledge" / "proj"; kdir.mkdir(parents=True)
    (kdir / "a.md").write_text("---\nslice_id: sl-aaaaaaaaaaaaaaaa\n---\nx\n", encoding="utf-8")
    _run(tmp_path, _view_payload("s1", str(kdir)))
    assert _events(tmp_path) == []


def test_non_view_tool_no_event(tmp_path):
    _run(tmp_path, {"sessionId": "s1", "cwd": "/x", "toolName": "bash",
                    "toolArgs": json.dumps({"command": "ls"}),
                    "toolResult": {"resultType": "success"}})
    assert _events(tmp_path) == []


def test_malformed_tool_args_no_event_exit0(tmp_path):
    _run(tmp_path, {"sessionId": "s1", "cwd": "/x", "toolName": "view",
                    "toolArgs": "not-json"})
    assert _events(tmp_path) == []


def test_view_offered_under_symlinked_memory_root(tmp_path):
    # 生產情境：memory root 是 symlink；offered map 存 un-resolved 路徑，
    # copilot 回報的 path 亦可能是該字串——resolve 後仍須對上。
    real = tmp_path / "real"
    (real / "knowledge" / "proj").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    note = link / "knowledge" / "proj" / "a.md"
    note.write_text("---\nslice_id: sl-cccccccccccccccc\nproject: proj\n---\nx\n", encoding="utf-8")
    _map(link, "s2", str(note), "sl-cccccccccccccccc")
    _run(link, _view_payload("s2", str(note)))
    ev = _events(link)
    assert len(ev) == 1 and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-cccccccccccccccc"
