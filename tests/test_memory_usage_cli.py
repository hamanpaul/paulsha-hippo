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
