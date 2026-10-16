"""Grok hook 契約：systemMessage、子 session 不截取、PostToolUse 不印 stdout。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "paulsha_hippo" / "hooks"


def _run(name: str, payload: dict, memory_root: Path, **env_extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(memory_root.parent),
        "PSC_MEMORY_ROOT": str(memory_root),
        "PSC_IMPORTER_DISABLED": "1",
        "PYTHONPATH": str(ROOT),
    }
    env.update(env_extra)
    memory_root.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(memory_root),
        env=env,
        timeout=30,
    )


def test_session_start_prints_only_system_message(tmp_path: Path) -> None:
    result = _run(
        "grok_session_start.py",
        {"sessionId": "s1", "cwd": str(tmp_path / "nowhere")},
        tmp_path / "memory",
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert set(parsed) == {"systemMessage"}
    assert isinstance(parsed["systemMessage"], str)
    assert "hookSpecificOutput" not in result.stdout
    assert "additionalContext" not in result.stdout


def test_session_end_skips_subagent_and_self_session(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    sub = _run(
        "grok_session_end.py",
        {"sessionId": "child", "cwd": str(tmp_path), "subagentType": "explore"},
        memory,
    )
    assert sub.returncode == 0, sub.stderr
    self_session = _run(
        "grok_session_end.py",
        {"session_id": "self", "cwd": str(tmp_path)},
        memory,
        HIPPO_SELF_SESSION="1",
    )
    assert self_session.returncode == 0, self_session.stderr
    queue = memory / "runtime" / "queue"
    items = list(queue.glob("*.json")) if queue.exists() else []
    assert items == []


def test_session_end_captures_parent_with_session_id(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    result = _run(
        "grok_session_end.py",
        {
            "sessionId": "parent",
            "cwd": str(tmp_path),
            "timestamp": "2026-10-16T10:00:00+00:00",
            "transcript_path": str(tmp_path / "chat_history.jsonl"),
        },
        memory,
    )
    assert result.returncode == 0, result.stderr
    items = list((memory / "runtime" / "queue").glob("grok__parent__*.json"))
    assert len(items) == 1
    written = json.loads(items[0].read_text(encoding="utf-8"))
    assert written["session_id"] == "parent"
    assert written["tool"] == "grok"
    assert written["capture_scope"] == "session_end"
    assert written["ended_at"] == "2026-10-16T10:00:00+00:00"


def _events(memory: Path) -> list[dict]:
    ledger = memory / "runtime" / "ledger" / "memory_usage.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


def test_post_tool_use_records_knowledge_read_without_stdout(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    note = memory / "knowledge" / "proj" / "a.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n---\nx\n", encoding="utf-8")

    recorded = _run(
        "grok_post_tool_use.py",
        {
            "sessionId": "s1",
            "tool_name": "read_file",
            "tool_input": json.dumps({"target_file": str(note)}),
        },
        memory,
    )
    assert recorded.returncode == 0, recorded.stderr
    assert recorded.stdout == ""
    events = _events(memory)
    assert len(events) == 1
    assert events[0]["tool"] == "grok"
    assert events[0]["source"] == "read"
    assert events[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa"
    assert events[0]["offered"] is False

    outside = memory / "elsewhere.md"
    outside.write_text("nope", encoding="utf-8")
    skipped = _run(
        "grok_post_tool_use.py",
        {"session_id": "s1", "toolName": "hashline_read", "toolInput": {"path": str(outside)}},
        memory,
    )
    assert skipped.returncode == 0, skipped.stderr
    assert skipped.stdout == ""
    assert len(_events(memory)) == 1
