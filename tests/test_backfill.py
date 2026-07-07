import json
from pathlib import Path

from paulsha_hippo.importer import backfill

FIX = Path(__file__).parent / "fixtures"


def _seed_queue(root: Path, transcript: str):
    q = root / "archive" / "queue" / "2026-06"
    q.mkdir(parents=True)
    (q / "claude-code__s1--written--abc.json").write_text(
        json.dumps({"tool": "claude-code", "session_id": "s1", "cwd": "/repo",
                    "transcript_path": transcript}),
        encoding="utf-8",
    )


def test_dry_run_does_not_write_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr("paulsha_hippo.importer.title._default_runner",
                        lambda t, c, to: "標題")
    _seed_queue(tmp_path, str(FIX / "claude_transcript.jsonl"))
    res = backfill.run(tmp_path, dry_run=True)
    assert res["count"] == 1
    assert not list((tmp_path / "inbox").rglob("*.md"))


def test_backfill_writes_content_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("paulsha_hippo.importer.title._default_runner",
                        lambda t, c, to: "標題")
    _seed_queue(tmp_path, str(FIX / "claude_transcript.jsonl"))
    backfill.run(tmp_path, dry_run=False)
    mds = list((tmp_path / "inbox").rglob("*.md"))
    assert len(mds) == 1
    body = mds[0].read_text(encoding="utf-8")
    assert "1. 幫我修 UART 升級流程" in body
    assert "title: 標題" in body
    assert "- /repo/uart.py" in body
    backfill.run(tmp_path, dry_run=False)  # re-run
    assert len(list((tmp_path / "inbox").rglob("*.md"))) == 1  # idempotent


def test_backfill_dead_pointer_leaves_empty(tmp_path):
    q = tmp_path / "archive" / "queue" / "2026-06"
    q.mkdir(parents=True)
    (q / "claude-code__d1--written--x.json").write_text(
        json.dumps({"tool": "claude-code", "session_id": "d1", "cwd": "/r",
                    "transcript_path": "/nonexistent.jsonl"}),
        encoding="utf-8",
    )
    res = backfill.run(tmp_path, dry_run=False)
    assert res["count"] == 1  # processed without crashing; content left empty
    md = list((tmp_path / "inbox").rglob("*.md"))[0].read_text(encoding="utf-8")
    assert "## Prompts\n- (none)" in md
