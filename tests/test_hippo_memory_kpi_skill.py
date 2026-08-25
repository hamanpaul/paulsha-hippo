from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paulsha_hippo import usage_read
from paulsha_hippo.lib.lifecycle import schema as lifecycle_schema
from paulsha_hippo.moc import frontmatter_io, search


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = (
    REPO_ROOT
    / "custom-skills"
    / "hippo-memory-kpi"
    / "scripts"
    / "report.py"
)
NOW = "2026-08-18T12:00:00Z"


def _write_jsonl(root: Path, name: str, rows: list[dict], *, bad_line: bool = False) -> None:
    ledger = root / "runtime" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    if bad_line:
        text = "not-json\n" + text
    (ledger / name).write_text(text, encoding="utf-8")


def _write_note(
    root: Path,
    *,
    slice_id: str,
    session_id: str,
    captured_at: str,
    artifact_kind: str,
    publication_id: str,
    valid_checksum: bool = True,
) -> Path:
    body = (
        f"This durable atomic note records deterministic KPI evidence for {slice_id}.\n"
        "It contains enough substantive detail for retrieval and later engineering reference.\n"
    )
    checksum = lifecycle_schema.compute_checksum(body)
    if not valid_checksum:
        checksum = "0" * 64
    phase = "review" if artifact_kind == "review" else "research"
    frontmatter = {
        "phase": phase,
        "project": "paulsha-hippo",
        "slice_id": slice_id,
        "artifact_kind": artifact_kind,
        "version": "1",
        "created_at": captured_at,
        "created_by": "claude-code",
        "source_session": session_id,
        "gate_required": False,
        "checksum": checksum,
        "memory_layer": "knowledge",
        "source_agent": "claude-code",
        "captured_at": captured_at,
        "provenance": {"repo": "hamanpaul/paulsha-hippo", "commit": "abc", "path": "tests"},
        "distiller": {},
        "supersedes": [],
        "distilled_from": f"claude-code:{session_id}",
        "title": f"KPI evidence {slice_id}",
        "atom_title": f"KPI evidence {slice_id}",
        # Invalid-checksum fixture also carries an invalid MOC tags type so it is
        # intentionally absent from the existing searchable index.  The KPI
        # report must still classify checksum validity independently.
        "tags": ["kpi", "memory"] if valid_checksum else [1],
        "publication_id": publication_id,
    }
    path = root / "knowledge" / "paulsha-hippo" / f"{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter_io.dump(frontmatter, body), encoding="utf-8")
    return path


def _seed_memory_root(root: Path, *, malformed_usage: bool = False) -> None:
    imports = [
        {"logical_session_key": "claude-code:s1", "captured_at": "2026-08-17T00:00:00Z", "status": "written"},
        {"logical_session_key": "claude-code:s2", "captured_at": "2026-08-16T00:00:00Z", "status": "written"},
        {"logical_session_key": "claude-code:s3", "captured_at": "2026-08-15T00:00:00Z", "status": "written"},
        {"logical_session_key": "claude-code:s4", "captured_at": "2026-08-10T00:00:00Z", "status": "empty-skip"},
        {"logical_session_key": "claude-code:s5", "captured_at": "2026-08-01T00:00:00Z", "status": "written"},
        {"logical_session_key": "claude-code:old", "captured_at": "2026-07-10T00:00:00Z", "status": "written"},
        {"logical_session_key": "codex:audit", "captured_at": "2026-08-18T00:00:00Z", "status": "written"},
    ]
    processing = [
        {"ts": "2026-08-17T12:00:00Z", "session_key": "claude-code:s1", "state": "promoted", "accepted_slices": 2},
        {"ts": "2026-08-16T12:00:00Z", "session_key": "claude-code:s2", "state": "no-findings", "accepted_slices": 0},
        {"ts": "2026-08-15T12:00:00Z", "session_key": "claude-code:s3", "state": "parked", "accepted_slices": 0},
        {"ts": "2026-08-01T12:00:00Z", "session_key": "claude-code:s5", "state": "promoted", "accepted_slices": 1},
        {"ts": "2026-07-10T12:00:00Z", "session_key": "claude-code:old", "state": "promoted", "accepted_slices": 1},
        {"ts": "2026-08-18T01:00:00Z", "session_key": "codex:audit", "state": "promoted", "accepted_slices": 1},
    ]
    _write_jsonl(root, "import.jsonl", imports)
    _write_jsonl(root, "processing.jsonl", processing)

    note_a = _write_note(
        root,
        slice_id="sl-aaaaaaaaaaaaaaaa",
        session_id="s1",
        captured_at="2026-08-17T12:00:00Z",
        artifact_kind="research",
        publication_id="pub-s1",
    )
    note_b = _write_note(
        root,
        slice_id="sl-bbbbbbbbbbbbbbbb",
        session_id="s1",
        captured_at="2026-08-17T12:00:00Z",
        artifact_kind="research",
        publication_id="pub-s1",
        valid_checksum=False,
    )
    note_c = _write_note(
        root,
        slice_id="sl-cccccccccccccccc",
        session_id="s5",
        captured_at="2026-08-01T12:00:00Z",
        artifact_kind="review",
        publication_id="pub-s5",
    )
    publications = [
        {
            "event": "publish_prepare",
            "publication_id": "pub-s1",
            "session_key": "claude-code:s1",
            "now": "2026-08-17T12:00:00Z",
            "items": [
                {"slice_id": "sl-aaaaaaaaaaaaaaaa", "target": str(note_a)},
                {"slice_id": "sl-bbbbbbbbbbbbbbbb", "target": str(note_b)},
            ],
        },
        {"event": "publish_commit", "publication_id": "pub-s1", "now": "2026-08-17T12:01:00Z"},
        {
            "event": "publish_prepare",
            "publication_id": "pub-s5",
            "session_key": "claude-code:s5",
            "now": "2026-08-01T12:00:00Z",
            "items": [{"slice_id": "sl-cccccccccccccccc", "target": str(note_c)}],
        },
        {"event": "publish_commit", "publication_id": "pub-s5", "now": "2026-08-01T12:01:00Z"},
    ]
    _write_jsonl(root, "publication.jsonl", publications)

    offered = [
        {"ts": "2026-08-17T10:00:00Z", "tool": "claude-code", "session_id": "s1", "offered": ["sl-aaaaaaaaaaaaaaaa", "sl-bbbbbbbbbbbbbbbb"]},
        {"ts": "2026-08-17T10:00:00Z", "tool": "claude-code", "session_id": "s2", "offered": ["sl-cccccccccccccccc"]},
        {"ts": "2026-08-18T10:00:00Z", "tool": "codex", "session_id": "audit", "offered": ["sl-eeeeeeeeeeeeeeee"]},
        {"ts": "2026-08-05T10:00:00Z", "tool": "codex", "session_id": "s3", "offered": ["sl-dddddddddddddddd"]},
        {"ts": "2026-07-25T10:00:00Z", "tool": "copilot-cli", "session_id": "s4", "offered": ["sl-ffffffffffffffff"]},
    ]
    usage = [
        {"ts": "2026-08-17T10:01:00Z", "tool": "claude-code", "session_id": "s1", "sl_id": "sl-aaaaaaaaaaaaaaaa", "source": "read"},
        {"ts": "2026-08-17T09:59:00Z", "tool": "claude-code", "session_id": "s1", "sl_id": "sl-bbbbbbbbbbbbbbbb", "source": "read"},
        {"ts": "2026-08-17T10:01:00Z", "tool": "claude-code", "session_id": "s2", "sl_id": "sl-cccccccccccccccc", "source": "read"},
        {"ts": "2026-08-18T10:01:00Z", "tool": "codex", "session_id": "audit", "sl_id": "sl-eeeeeeeeeeeeeeee", "source": "read"},
        {"ts": "2026-08-05T10:01:00Z", "tool": "codex", "session_id": "s3", "sl_id": "sl-dddddddddddddddd", "source": "read"},
        {"ts": "2026-07-25T10:01:00Z", "tool": "copilot-cli", "session_id": "s4", "sl_id": "sl-ffffffffffffffff", "source": "read"},
        {"ts": "2026-08-17T10:01:00Z", "tool": "claude-code", "session_id": "sx", "sl_id": "sl-gggggggggggggggg", "source": "read"},
        {"ts": "2026-08-17T10:02:00Z", "tool": "claude-code", "session_id": "s1", "slice_id": "sl-aaaaaaaaaaaaaaaa", "kind": "applied"},
        {"ts": "2026-08-17T10:03:00Z", "tool": "claude-code", "session_id": "s1", "slice_id": "sl-bbbbbbbbbbbbbbbb", "kind": "applied"},
        {"ts": "2026-08-05T09:30:00Z", "tool": "codex", "session_id": "s3", "slice_id": "sl-dddddddddddddddd", "kind": "applied"},
        {"ts": "2026-07-25T10:03:00Z", "tool": "copilot-cli", "session_id": "s4", "slice_id": "sl-ffffffffffffffff", "kind": "applied"},
        {"ts": "2026-08-17T10:04:00Z", "tool": "claude-code", "session_id": "sx", "slice_id": "sl-gggggggggggggggg", "kind": "applied"},
    ]
    _write_jsonl(root, "offered.jsonl", offered)
    _write_jsonl(root, "memory_usage.jsonl", usage, bad_line=malformed_usage)

    coverage = search.build_index(root, link_weights={})
    assert coverage["eligible"] == 1
    assert coverage["indexed"] == 1


def _run_report(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--memory-root",
            str(root),
            "--now",
            NOW,
            "--days",
            "7",
            "--days",
            "30",
            "--exclude-session",
            "codex:audit",
            *extra,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_report_json_enforces_conversion_quality_and_ordered_usage_contract(tmp_path):
    _seed_memory_root(tmp_path)

    completed = _run_report(tmp_path, "--format", "json")

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["schema_version"] == "hippo-memory-kpi/v1"
    assert report["excluded_sessions"] == ["codex:audit"]

    seven = report["windows"]["7d"]
    assert seven["session_conversion"] == {
        "intake_sessions": 3,
        "empty_skip": 0,
        "skipped_sessions": 0,
        "skip_status_counts": {},
        "nonempty_sessions": 3,
        "promoted": 1,
        "no_findings": 1,
        "parked": 1,
        "pending": 0,
        "other_processing_states": {},
        "accepted_notes": 2,
        "conversion": {"numerator": 1, "denominator": 3, "rate_pct": 33.33},
        "substantive_conversion": {"numerator": 1, "denominator": 2, "rate_pct": 50.0},
    }
    assert seven["note_quality"]["produced_unique_notes"] == 2
    assert seven["note_quality"]["machine_valid"] == {"count": 1, "denominator": 2, "rate_pct": 50.0}
    assert seven["note_quality"]["searchable"] == {"count": 1, "denominator": 2, "rate_pct": 50.0}
    assert seven["note_quality"]["adjusted_non_review_searchable"] == {"count": 1, "denominator": 1, "rate_pct": 100.0}
    assert seven["usage"]["offered_unique_notes"] == 2
    assert seven["usage"]["raw_read_events"] == 3
    assert seven["usage"]["raw_read_unique_notes"] == 3
    assert seven["usage"]["viewed"] == {"count": 1, "denominator": 2, "rate_pct": 50.0}
    assert seven["usage"]["strict_adopted"] == {"count": 1, "denominator": 2, "rate_pct": 50.0}

    thirty = report["windows"]["30d"]
    assert thirty["session_conversion"]["intake_sessions"] == 5
    assert thirty["session_conversion"]["empty_skip"] == 1
    assert thirty["session_conversion"]["nonempty_sessions"] == 4
    assert thirty["session_conversion"]["promoted"] == 2
    assert thirty["session_conversion"]["accepted_notes"] == 3
    assert thirty["session_conversion"]["conversion"] == {"numerator": 2, "denominator": 4, "rate_pct": 50.0}
    assert thirty["note_quality"]["produced_unique_notes"] == 3
    assert thirty["note_quality"]["machine_valid"] == {"count": 2, "denominator": 3, "rate_pct": 66.67}
    assert thirty["note_quality"]["searchable"] == {"count": 1, "denominator": 3, "rate_pct": 33.33}
    assert thirty["note_quality"]["review_pool_excluded"] == 1
    assert thirty["note_quality"]["adjusted_non_review_searchable"] == {"count": 1, "denominator": 1, "rate_pct": 100.0}
    assert thirty["usage"]["offered_unique_notes"] == 4
    assert thirty["usage"]["viewed"] == {"count": 3, "denominator": 4, "rate_pct": 75.0}
    assert thirty["usage"]["strict_adopted"] == {"count": 2, "denominator": 4, "rate_pct": 50.0}
    assert thirty["usage"]["loose_offer_applied"] == {"count": 3, "denominator": 4, "rate_pct": 75.0}
    assert thirty["usage"]["raw_read_events"] == 5
    assert thirty["usage"]["raw_read_unique_notes"] == 5
    assert thirty["usage"]["direct_read_events"] == 2
    assert report["attribution_limits"]["codex"] == "lower-bound"
    assert "reporter_build_identity" in report
    assert "runtime_identity" not in report


def test_report_is_read_only_and_surfaces_bounded_parse_diagnostics(tmp_path):
    _seed_memory_root(tmp_path, malformed_usage=True)
    before = _snapshot(tmp_path)

    completed = _run_report(tmp_path, "--format", "json")

    assert completed.returncode == 0, completed.stderr
    assert _snapshot(tmp_path) == before
    report = json.loads(completed.stdout)
    assert report["diagnostics"]["memory_usage.jsonl"]["json_decode_error"] == 1
    assert report["windows"]["30d"]["usage"]["viewed"]["rate_pct"] == 75.0


def test_report_markdown_keeps_proxies_and_cortex_caveats_explicit(tmp_path):
    _seed_memory_root(tmp_path)

    completed = _run_report(tmp_path, "--format", "markdown")

    assert completed.returncode == 0, completed.stderr
    assert "| 指標 | 7 天 | 30 天 |" in completed.stdout
    assert "機器可讀 proxy" in completed.stdout
    assert "不是人類語意可讀性" in completed.stdout
    assert "offer → read → applied" in completed.stdout
    assert "Cortex source_material" in completed.stdout
    assert "Codex" in completed.stdout and "可觀測下限" in completed.stdout
    assert "codex:audit" in completed.stdout


def test_report_treats_index_integrity_problems_as_unavailable(tmp_path):
    _seed_memory_root(tmp_path)
    conn = sqlite3.connect(search.index_path(tmp_path))
    try:
        conn.execute(
            "DELETE FROM slices_fts WHERE slice_id = ?",
            ("sl-aaaaaaaaaaaaaaaa",),
        )
        conn.commit()
    finally:
        conn.close()

    completed = _run_report(tmp_path, "--format", "json")

    assert completed.returncode == 0, completed.stderr
    quality = json.loads(completed.stdout)["windows"]["7d"]["note_quality"]
    assert quality["index_available"] is False
    assert quality["searchable"] == {"count": None, "denominator": 2, "rate_pct": None}
    assert quality["adjusted_non_review_searchable"] == {
        "count": None,
        "denominator": 1,
        "rate_pct": None,
    }
    assert quality["index_problems"]


def test_report_ignores_publications_committed_after_snapshot(tmp_path):
    _seed_memory_root(tmp_path)
    publication = tmp_path / "runtime" / "ledger" / "publication.jsonl"
    with publication.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "publish_prepare",
                    "publication_id": "pub-future",
                    "session_key": "claude-code:s1",
                    "now": "2026-08-17T13:00:00Z",
                    "items": [
                        {
                            "slice_id": "sl-futurefuturefuture",
                            "target": str(tmp_path / "knowledge" / "future.md"),
                        }
                    ],
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "event": "publish_commit",
                    "publication_id": "pub-future",
                    "now": "2026-08-19T00:00:00Z",
                }
            )
            + "\n"
        )

    completed = _run_report(tmp_path, "--format", "json")

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["windows"]["7d"]["note_quality"]["produced_unique_notes"] == 2


def test_report_marks_missing_sources_and_zero_denominators_as_unavailable(tmp_path):
    tmp_path.mkdir(exist_ok=True)

    completed = _run_report(tmp_path, "--format", "json")

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert all(
        report["diagnostics"][name]["missing_file"] == 1
        for name in (
            "import.jsonl",
            "processing.jsonl",
            "publication.jsonl",
            "offered.jsonl",
            "memory_usage.jsonl",
        )
    )
    seven = report["windows"]["7d"]
    assert seven["session_conversion"]["conversion"] == {
        "numerator": 0,
        "denominator": 0,
        "rate_pct": None,
    }
    assert seven["note_quality"]["machine_valid"] == {
        "count": 0,
        "denominator": 0,
        "rate_pct": None,
    }
    assert seven["note_quality"]["searchable"] == {
        "count": None,
        "denominator": 0,
        "rate_pct": None,
    }
    assert seven["usage"]["viewed"] == {
        "count": 0,
        "denominator": 0,
        "rate_pct": None,
    }

    markdown = _run_report(tmp_path, "--format", "markdown")
    assert markdown.returncode == 0, markdown.stderr
    assert "## 資料診斷" in markdown.stdout
    assert "missing_file=1" in markdown.stdout


def test_show_agent_read_event_counts_toward_viewed_kpi(tmp_path):
    """`hippo show --agent` 取代 Read 之後，記下的 read 事件仍要餵得動看過率 KPI。

    fix 3b 的重點：省下的 Read 不能讓 KPI 看不到——offered.jsonl 先有一筆
    shortlist offer，usage_read.append_read_event（show 的 read 歸因寫入器）
    補一筆同 schema 的 read 事件後，report 的 usage.viewed 就要把它算進去。
    """
    root = tmp_path
    note = root / "knowledge" / "proj" / "note--sl-aaaaaaaaaaaaaaaa.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n---\nbody\n", encoding="utf-8"
    )
    offer_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_jsonl(root, "offered.jsonl", [
        {"ts": offer_ts, "tool": "claude-code", "session_id": "s1", "offered": ["sl-aaaaaaaaaaaaaaaa"]},
    ])

    usage_read.append_read_event(
        root, tool="claude-code", session_id="s1",
        sl_id="sl-aaaaaaaaaaaaaaaa", path=note, project="proj",
    )

    completed = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), "--memory-root", str(root), "--format", "json"],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["windows"]["7d"]["usage"]["viewed"]["count"] == 1
