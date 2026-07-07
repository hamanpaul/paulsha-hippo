# tests/test_post_tool_use_hook.py
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/claude_post_tool_use.py").resolve()


def _map(mr: Path, sid: str, path: str, slid: str):
    wk = mr / "runtime" / "wakeup"; wk.mkdir(parents=True, exist_ok=True)
    (wk / f"claude-code__{sid}.offered.json").write_text(
        json.dumps({"by_path": {path: slid}, "by_id": {slid: path}}), encoding="utf-8")


def _run(mr: Path, payload: dict):
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr


def _events(mr: Path):
    f = mr / "runtime" / "ledger" / "memory_usage.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_read_offered_knowledge_path_records_used(tmp_path):
    note = tmp_path / "knowledge" / "proj" / "a.md"; note.parent.mkdir(parents=True)
    note.write_text("---\nslice_id: sl-aaaaaaaaaaaaaaaa\n---\nx\n", encoding="utf-8")
    _map(tmp_path, "s1", str(note), "sl-aaaaaaaaaaaaaaaa")
    _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read",
                    "tool_input": {"file_path": str(note)}, "cwd": "/x"})
    ev = _events(tmp_path)
    assert len(ev) == 1 and ev[0]["source"] == "read" and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa"


def test_read_non_offered_knowledge_records_offered_false(tmp_path):
    note = tmp_path / "knowledge" / "proj" / "b.md"; note.parent.mkdir(parents=True)
    note.write_text("---\nslice_id: sl-bbbbbbbbbbbbbbbb\n---\nx\n", encoding="utf-8")
    _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s9", "tool_name": "Read",
                    "tool_input": {"file_path": str(note)}, "cwd": "/x"})
    ev = _events(tmp_path)
    assert len(ev) == 1 and ev[0]["offered"] is False and ev[0]["sl_id"] == "sl-bbbbbbbbbbbbbbbb"


def test_read_non_knowledge_path_no_event(tmp_path):
    other = tmp_path / "elsewhere.md"; other.write_text("hi", encoding="utf-8")
    _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read",
                    "tool_input": {"file_path": str(other)}, "cwd": "/x"})
    assert _events(tmp_path) == []


def test_non_read_tool_no_event(tmp_path):
    _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
                    "tool_input": {"command": "ls"}, "cwd": "/x"})
    assert _events(tmp_path) == []


def test_read_offered_under_symlinked_memory_root(tmp_path):
    # Production: ~/.agents/memory is a symlink. build_index stores the UN-resolved path
    # in the offered map; the agent Reads that same string; the hook must still match.
    real = tmp_path / "real"
    (real / "knowledge" / "proj").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    note = link / "knowledge" / "proj" / "a.md"  # un-resolved (symlinked) path
    note.write_text("---\nslice_id: sl-cccccccccccccccc\nproject: proj\n---\nx\n", encoding="utf-8")
    _map(link, "s2", str(note), "sl-cccccccccccccccc")
    _run(link, {"hook_event_name": "PostToolUse", "session_id": "s2", "tool_name": "Read",
                "tool_input": {"file_path": str(note)}, "cwd": "/x"})
    ev = _events(link)
    assert len(ev) == 1 and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-cccccccccccccccc"
