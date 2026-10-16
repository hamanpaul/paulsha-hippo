"""Grok chat_history.jsonl adapter：略過合成列，只收集寫入路徑。"""
from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo.importer.adapters import grok
from paulsha_hippo.importer.pipeline import _extract


def _history() -> str:
    rows = [
        {"type": "system", "content": "boot"},
        {
            "type": "user",
            "prompt_index": 0,
            "content": [{"type": "text", "text": "real prompt"}],
        },
        {
            "type": "user",
            "synthetic_reason": "compact",
            "content": [{"type": "text", "text": "synthetic prompt"}],
        },
        {"type": "reasoning", "summary": []},
        {
            "type": "assistant",
            "content": "did the edit",
            "tool_calls": [
                {
                    "id": "1",
                    "name": "search_replace",
                    "arguments": json.dumps({"file_path": "src/a.py"}),
                },
                {
                    "id": "2",
                    "name": "read_file",
                    "arguments": json.dumps({"target_file": "README.md"}),
                },
                {
                    "id": "3",
                    "name": "write",
                    "arguments": {"path": "src/b.py"},
                },
                {
                    "id": "4",
                    "name": "hashline_edit",
                    "arguments": json.dumps({"target_file": "src/a.py"}),
                },
            ],
        },
        {"type": "tool_result", "tool_call_id": "1", "content": "ok"},
        {"type": "backend_tool_call", "name": "noop"},
    ]
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def test_read_grok_transcript_skips_synthetic_and_read_tools(tmp_path: Path) -> None:
    history = tmp_path / "chat_history.jsonl"
    history.write_text(_history(), encoding="utf-8")

    extracted = grok.read_grok_transcript(history)

    assert extracted["user_prompts"] == ["real prompt"]
    assert extracted["assistant_messages"] == ["did the edit"]
    assert extracted["touched_files"] == ["src/a.py", "src/b.py"]


def test_transcript_path_accepts_session_dir_and_events_sibling(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "chat_history.jsonl").write_text(_history(), encoding="utf-8")
    (session / "events.jsonl").write_text("{}\n", encoding="utf-8")

    from_dir = grok.read_grok_transcript(session)
    from_events = grok.read_grok_transcript(session / "events.jsonl")

    assert from_dir["user_prompts"] == ["real prompt"]
    assert from_events["touched_files"] == ["src/a.py", "src/b.py"]
    assert grok.read_grok_transcript(tmp_path / "missing")["user_prompts"] == []


def test_pipeline_extract_dispatches_grok(tmp_path: Path) -> None:
    history = tmp_path / "chat_history.jsonl"
    history.write_text(_history(), encoding="utf-8")
    queue = tmp_path / "item.json"
    queue.write_text(
        json.dumps(
            {
                "tool": "grok",
                "sessionId": "sid-grok",
                "transcript_path": str(history),
                "cwd": "/work",
                "timestamp": "2026-10-16T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = _extract(queue)

    assert result.session["tool"] == "grok"
    assert result.session["session_id"] == "sid-grok"
    assert result.session["user_prompts"] == ["real prompt"]
    assert result.session["touched_files"] == ["src/a.py", "src/b.py"]
    assert result.session["ended_at"] == "2026-10-16T10:00:00+00:00"
    assert result.capture_scope == "session_end"
