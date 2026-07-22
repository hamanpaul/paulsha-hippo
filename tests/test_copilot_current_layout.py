from __future__ import annotations

import json

import pytest

from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import pipeline as atomizer_pipeline
from paulsha_hippo.importer.adapters import copilot as copilot_adapter
from paulsha_hippo.importer import pipeline as importer_pipeline
from paulsha_hippo.importer import title as importer_title
from paulsha_hippo.lib.session_readers import read_copilot_history


def test_current_copilot_session_state_events_are_read(tmp_path):
    path = tmp_path / ".copilot" / "session-state" / "s1" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "user.message", "data": {"content": "fix UART"}}),
                json.dumps({"type": "assistant.message", "data": {"content": "root cause found"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = read_copilot_history(tmp_path, "s1")
    assert result["user_prompts"] == ["fix UART"]
    assert result["assistant_messages"] == ["root cause found"]
    assert result["assistant_summary"] == "root cause found"


def test_copilot_adapter_retries_session_end_flush_race(tmp_path, monkeypatch):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({
        "tool": "copilot-cli",
        "session_id": "s-race",
        "psc_config_root": str(tmp_path),
        "capture_scope": "session_end",
        "cwd": str(tmp_path),
    }), encoding="utf-8")
    snapshots = iter([
        {"user_prompts": [], "assistant_messages": [], "assistant_summary": ""},
        {
            "user_prompts": ["verify producer"],
            "assistant_messages": ["producer verified"],
            "assistant_summary": "producer verified",
        },
    ])
    sleeps: list[float] = []
    monkeypatch.setattr(copilot_adapter, "read_copilot_history", lambda *_: next(snapshots))
    monkeypatch.setattr(copilot_adapter.time, "sleep", sleeps.append)

    result = copilot_adapter.extract(queue)

    assert result.session["user_prompts"] == ["verify producer"]
    assert result.session["assistant_messages"] == ["producer verified"]
    assert sleeps == [0.05]


def test_copilot_adapter_flush_retry_is_bounded(tmp_path, monkeypatch):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({
        "tool": "copilot-cli",
        "session_id": "s-empty",
        "psc_config_root": str(tmp_path),
        "capture_scope": "session_end",
        "cwd": str(tmp_path),
    }), encoding="utf-8")
    calls = 0
    sleeps: list[float] = []

    def empty_history(*_):
        nonlocal calls
        calls += 1
        return {"user_prompts": [], "assistant_messages": [], "assistant_summary": ""}

    monkeypatch.setattr(copilot_adapter, "read_copilot_history", empty_history)
    monkeypatch.setattr(copilot_adapter.time, "sleep", sleeps.append)

    result = copilot_adapter.extract(queue)

    assert result.session["user_prompts"] == []
    assert result.session["assistant_messages"] == []
    assert calls == 5
    assert sleeps == [0.05, 0.1, 0.2, 0.4]


@pytest.mark.parametrize("layout", ["current", "legacy"])
def test_copilot_layout_runs_through_importer_inbox_and_atom(tmp_path, monkeypatch, layout):
    copilot_root = tmp_path / ".copilot"
    session_id = f"s-{layout}"
    if layout == "current":
        events = copilot_root / "session-state" / session_id / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text(
            json.dumps({"type": "user.message", "data": {"content": "implement driver"}})
            + "\n"
            + json.dumps({"type": "assistant.message", "data": {"content": "driver implemented"}})
            + "\n",
            encoding="utf-8",
        )
    else:
        history = copilot_root / "history-session-state" / f"session_{session_id}_20260722.json"
        history.parent.mkdir(parents=True)
        history.write_text(
            json.dumps({"chatMessages": [
                {"role": "user", "content": "implement driver"},
                {"role": "assistant", "content": "driver implemented"},
            ]}),
            encoding="utf-8",
        )
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({
        "tool": "copilot-cli",
        "sessionId": session_id,
        "psc_config_root": str(copilot_root),
        "capture_scope": "session_end",
        "ended_at": "2026-07-22T00:00:00Z",
        "cwd": str(tmp_path),
        "repo": "demo-project",
        "turn_count": 2,
        "touched_files": ["driver.py"],
    }), encoding="utf-8")
    monkeypatch.setattr(importer_title, "_default_runner", lambda prompt, command, timeout: "driver implementation")

    decision = importer_pipeline.ingest_queue_item(queue, memory_root=tmp_path)
    cfg, config_hash = atomizer_config.load_config(override_path=None)
    result = atomizer_pipeline.run(tmp_path, config=cfg, config_hash=config_hash, now="2026-07-22T01:00:00Z")

    assert decision["status"] == "written"
    assert result["summary"]["slices"] == 1
    assert result["produced_slice_ids"]
    notes = list((tmp_path / "knowledge").rglob("*.md"))
    assert len(notes) == 1
    assert "driver implemented" in notes[0].read_text(encoding="utf-8")
