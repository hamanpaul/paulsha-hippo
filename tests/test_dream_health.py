from __future__ import annotations

from pathlib import Path

from paulsha_hippo.dream import orchestrator
from paulsha_hippo.ledger import dream, processing


def test_backlog_census_distinguishes_current_states_and_retrying(tmp_path: Path):
    processing.append_state(
        tmp_path, session_key="claude:split", state="split",
        now="2026-07-21T00:00:00Z", config_hash="c", attempts=2,
    )
    processing.append_state(
        tmp_path, session_key="codex:parked", state="parked",
        now="2026-07-21T01:00:00Z", config_hash="c", attempts=6,
        failure_category="transient",
    )
    processing.append_state(
        tmp_path, session_key="agy:quarantine", state="quarantined",
        now="2026-07-21T02:00:00Z", config_hash="c",
    )
    processing.append_state(
        tmp_path, session_key="cg:promoted", state="promoted",
        now="2026-07-21T03:00:00Z", config_hash="c",
    )

    census = dream.backlog_census(tmp_path, now="2026-07-22T00:00:00Z")

    assert census["split"] == 1
    assert census["retrying"] == 1
    assert census["parked"] == 1
    assert census["quarantined"] == 1
    assert census["promoted"] == 1
    assert census["reason_counts"] == {"transient": 1}
    assert census["oldest_backlog_age_seconds"] >= 23 * 60 * 60


def test_dream_health_contains_run_and_integrity_identity(tmp_path: Path):
    record = orchestrator.run_dream(
        tmp_path,
        atomize_fn=lambda: {
            "summary": {"skipped": 0, "backend_identity": "external-cli"},
            "warnings": [],
            "produced_slice_ids": ["sl-1"],
        },
        janitor_fn=lambda: {"summary": {"skipped": 0}, "warnings": []},
        now="2026-07-22T00:00:00Z",
        config_hash="config-hash",
    )

    health = record["health"]
    assert health["run_id"] == "dream-2026-07-22T00:00:00Z"
    assert health["produced_slice_ids"] == ["sl-1"]
    assert health["notes_created"] == 1
    assert health["backend_identity"] == "external-cli"
    for key in ("generic_title", "unknown_project", "invalid_frontmatter", "invalid_checksum"):
        assert key in health
