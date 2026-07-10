from __future__ import annotations

import argparse
import json

from paulsha_hippo.cli import _memory_usage


def test_memory_usage_read_based(tmp_path, capsys):
    led = tmp_path / "runtime" / "ledger"
    led.mkdir(parents=True)
    (led / "offered.jsonl").write_text(
        json.dumps({"ts": "2026-06-29T01:00:00Z", "session_id": "s", "tool": "claude-code",
                    "project": "p", "offered": [{"sl_id": "sl-a", "path": "/k/a.md"},
                                                {"sl_id": "sl-b", "path": "/k/b.md"}]}) + "\n",
        encoding="utf-8")
    (led / "memory_usage.jsonl").write_text(
        json.dumps({"ts": "2026-06-29T01:05:00Z", "session_id": "s", "tool": "claude-code",
                    "project": "p", "sl_id": "sl-a", "path": "/k/a.md",
                    "source": "read", "offered": True}) + "\n",
        encoding="utf-8")
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=True)
    assert _memory_usage(args) == 0
    rep = json.loads(capsys.readouterr().out)
    by = {s["slice_id"]: s for s in rep["slices"]}
    assert by["sl-a"]["offered_count"] == 1 and by["sl-a"]["read_count"] == 1
    assert by["sl-b"]["offered_count"] == 1 and by["sl-b"]["read_count"] == 0
    assert rep["summary"]["never_read"] == 1  # sl-b offered but never read


def test_memory_usage_empty_ledger(tmp_path, capsys):
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=True)
    assert _memory_usage(args) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["summary"]["sessions"] == 0
    assert rep["summary"]["never_read"] == 0
    assert rep["summary"]["total_reads"] == 0
    assert rep["slices"] == []


def test_memory_usage_by_tool_with_applied_and_na(tmp_path, capsys):
    led = tmp_path / "runtime" / "ledger"
    led.mkdir(parents=True)
    (led / "offered.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:00:00Z", "session_id": "s1", "tool": "claude-code",
                    "project": "p", "offered": [{"sl_id": "sl-a", "path": "/k/a.md"}]}) + "\n" +
        json.dumps({"ts": "2026-07-10T01:01:00Z", "session_id": "s2", "tool": "codex",
                    "project": "p", "offered": [{"sl_id": "sl-b", "path": "/k/b.md"},
                                                {"sl_id": "sl-c", "path": "/k/c.md"}]}) + "\n",
        encoding="utf-8")
    (led / "memory_usage.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:05:00Z", "session_id": "s1", "tool": "claude-code",
                    "project": "p", "sl_id": "sl-a", "path": "/k/a.md",
                    "source": "read", "offered": True}) + "\n" +
        json.dumps({"kind": "applied", "session_id": "s1", "slice_id": "sl-a",
                    "tool": "claude-code", "ts": "2026-07-10T01:06:00Z"}) + "\n",
        encoding="utf-8")
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=True)
    assert _memory_usage(args) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["by_tool"]["claude-code"] == {"offered": 1, "read": 1, "applied": 1}
    # codex：offered 2、read 0、applied 無訊號 → null（n/a），不猜測補值
    assert rep["by_tool"]["codex"] == {"offered": 2, "read": 0, "applied": None}
    # applied 事件不得污染 read 聚合
    assert rep["summary"]["total_reads"] == 1


def test_memory_usage_text_mode_renders_applied_na(tmp_path, capsys):
    led = tmp_path / "runtime" / "ledger"
    led.mkdir(parents=True)
    (led / "offered.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:00:00Z", "session_id": "s2", "tool": "codex",
                    "project": "p", "offered": [{"sl_id": "sl-b", "path": "/k/b.md"}]}) + "\n",
        encoding="utf-8")
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=False)
    assert _memory_usage(args) == 0
    out = capsys.readouterr().out
    assert "tool=codex offered=1 read=0 applied=n/a" in out
