from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo import cli


def _write_ledger(root, name: str, rows: list[dict]) -> None:
    ledger = root / "runtime" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / name).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_usage_funnel_accepts_legacy_and_object_offers_and_counts_sessions(
    tmp_path, capsys
):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-legacy",
                "tool": "claude-code",
                "offered": ["sl-legacy"],
            },
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-object",
                "tool": "codex",
                "offered": [{"sl_id": "sl-object", "path": "/k/object.md"}],
            },
            {
                "ts": "2026-07-01T00:02:00Z",
                "session_id": "s-empty",
                "tool": "codex",
                "offered": [],
            },
            {
                "ts": "2026-07-01T00:03:00Z",
                "session_id": "s-copilot",
                "tool": "copilot-cli",
                "offered": [{"sl_id": "sl-copilot", "path": "/k/copilot.md"}],
            },
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-07-01T00:10:00Z",
                "session_id": "s-legacy",
                "tool": "claude-code",
                "sl_id": "sl-legacy",
                "source": "read",
            },
            {
                "ts": "2026-07-01T00:11:00Z",
                "session_id": "s-object",
                "tool": "codex",
                "sl_id": "sl-object",
                "source": "read",
            },
            {
                "ts": "2026-07-01T00:12:00Z",
                "session_id": "s-legacy",
                "tool": "claude-code",
                "kind": "applied",
                "slice_id": "sl-legacy",
            },
        ],
    )

    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["summary"] == {
        "sessions_offered": 3,
        "sessions_with_read": 2,
        "read_through_rate": 66.67,
        "sessions_with_applied": 1,
        "applied_rate": 33.33,
    }
    assert report["by_tool"]["claude-code"] == {
        "sessions_offered": 1,
        "sessions_with_read": 1,
        "read_through_rate": 100.0,
        "sessions_with_applied": 1,
        "applied_rate": 100.0,
    }
    assert report["by_tool"]["codex"] == {
        "sessions_offered": 1,
        "sessions_with_read": 1,
        "read_through_rate": 100.0,
        "sessions_with_applied": 0,
        "applied_rate": 0.0,
    }
    assert report["by_tool"]["copilot-cli"]["sessions_offered"] == 1
    assert report["by_tool"]["copilot-cli"]["sessions_with_read"] == 0
    assert report["by_tool"]["copilot-cli"]["sessions_with_applied"] == 0

    slices = {item["slice_id"]: item for item in report["top_slices"]}
    assert slices["sl-legacy"] == {
        "slice_id": "sl-legacy",
        "offered_count": 1,
        "read_count": 1,
        "read_offer_ratio": 1.0,
    }
    assert slices["sl-object"]["offered_count"] == 1
    assert slices["sl-object"]["read_count"] == 1


def test_usage_funnel_empty_data_has_zero_rates(tmp_path, capsys):
    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["summary"] == {
        "sessions_offered": 0,
        "sessions_with_read": 0,
        "read_through_rate": 0.0,
        "sessions_with_applied": 0,
        "applied_rate": 0.0,
    }
    assert report["top_slices"] == []
    assert all(
        metrics["sessions_offered"] == 0
        for metrics in report["by_tool"].values()
    )


def test_usage_funnel_does_not_round_small_slice_ratio_to_zero(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-many-offers",
                "tool": "claude-code",
                "offered": ["sl-many"] * 10_000,
            }
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-many-offers",
                "tool": "claude-code",
                "sl_id": "sl-many",
                "source": "read",
            }
        ],
    )

    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["top_slices"][0]["read_offer_ratio"] == 0.0001


def test_usage_funnel_since_filters_all_event_streams(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-06-30T23:59:59Z",
                "session_id": "s-before",
                "tool": "claude-code",
                "offered": ["sl-before"],
            },
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-after",
                "tool": "codex",
                "offered": [{"sl_id": "sl-after", "path": "/k/after.md"}],
            },
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-06-30T23:59:59Z",
                "session_id": "s-before",
                "tool": "claude-code",
                "sl_id": "sl-before",
                "source": "read",
            },
            {
                "ts": "2026-07-01T00:00:01Z",
                "session_id": "s-after",
                "tool": "codex",
                "sl_id": "sl-after",
                "source": "read",
            },
        ],
    )

    assert (
        cli.main(
            [
                "usage",
                "funnel",
                "--memory-root",
                str(tmp_path),
                "--since",
                "2026-07-01T00:00:00Z",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["summary"]["sessions_offered"] == 1
    assert report["summary"]["sessions_with_read"] == 1
    assert report["summary"]["read_through_rate"] == 100.0
    assert [item["slice_id"] for item in report["top_slices"]] == ["sl-after"]


def test_usage_funnel_text_mode_is_human_readable(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s1",
                "tool": "claude-code",
                "offered": ["sl-a"],
            }
        ],
    )

    assert cli.main(["usage", "funnel", "--memory-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "sessions_offered=1" in out
    assert "read-through=0.00%" in out
    assert "tool=claude-code" in out
    assert "top_slices" in out


def test_usage_funnel_reports_noise_in_both_modes(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-noise",
                "tool": "claude-code",
                "offered": ["sl-noise"],
            },
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-clean",
                "tool": "copilot-cli",
                "offered": ["sl-clean"],
            },
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-noise",
                "tool": "claude-code",
                "sl_id": "sl-noise",
                "source": "read",
            },
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-clean",
                "tool": "copilot-cli",
                "sl_id": "sl-clean",
                "source": "read",
            },
        ],
    )
    _write_ledger(
        tmp_path,
        "processing.jsonl",
        [
            {
                "ts": "2026-06-30T23:59:00Z",
                "session_key": "claude-code:s-noise",
                "state": "split",
            },
            {
                "ts": "2026-07-01T00:02:00Z",
                "session_key": "claude-code:s-noise",
                "state": "no-findings",
            },
        ],
    )

    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["selected_mode"] == "without-noise"
    assert report["with_noise"]["summary"]["sessions_offered"] == 2
    assert report["with_noise"]["summary"]["sessions_with_read"] == 2
    assert report["without_noise"]["summary"]["sessions_offered"] == 1
    assert report["without_noise"]["summary"]["sessions_with_read"] == 1
    assert report["without_noise"]["by_tool"]["claude-code"]["sessions_offered"] == 0
    assert report["without_noise"]["by_tool"]["copilot-cli"]["sessions_offered"] == 1
    assert report["summary"] == report["without_noise"]["summary"]
    assert report["by_tool"] == report["without_noise"]["by_tool"]

    assert (
        cli.main(
            [
                "usage",
                "funnel",
                "--memory-root",
                str(tmp_path),
                "--include-noise",
                "--json",
            ]
        )
        == 0
    )
    include_noise_report = json.loads(capsys.readouterr().out)
    assert include_noise_report["selected_mode"] == "with-noise"
    assert include_noise_report["summary"] == report["with_noise"]["summary"]


def test_usage_funnel_does_not_count_reads_without_a_prior_offer(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-partial",
                "tool": "claude-code",
                "offered": ["sl-offered"],
            }
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-partial",
                "tool": "claude-code",
                "sl_id": "sl-not-offered",
                "source": "read",
            },
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-direct",
                "tool": "claude-code",
                "sl_id": "sl-direct",
                "source": "read",
            },
        ],
    )

    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["summary"]["sessions_offered"] == 1
    assert report["summary"]["sessions_with_read"] == 0
    assert report["summary"]["read_through_rate"] == 0.0
    assert report["read_attribution"] == {
        "offer_then_read_events": 0,
        "offer_then_read_sessions": 0,
        "direct_read_events": 2,
        "direct_read_sessions": 2,
        "by_tool": {
            "claude-code": {
                "offer_then_read_events": 0,
                "offer_then_read_sessions": 0,
                "direct_read_events": 2,
                "direct_read_sessions": 2,
            }
        },
    }
    assert report["top_slices"] == []


def test_usage_funnel_enforces_task8_contract_dimensions_and_installed_command(tmp_path, capsys):
    _write_ledger(
        tmp_path,
        "offered.jsonl",
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "session_id": "s-offered",
                "tool": "claude-code",
                "offered": ["sl-offered-1", "sl-offered-2"],
            },
            {
                "ts": "2026-07-01T00:00:01Z",
                "session_id": "s-copilot",
                "tool": "copilot-cli",
                "offered": [{"sl_id": "sl-offered-3", "path": "/k/copilot.md"}],
            },
        ],
    )
    _write_ledger(
        tmp_path,
        "memory_usage.jsonl",
        [
            {
                "ts": "2026-07-01T00:01:00Z",
                "session_id": "s-offered",
                "tool": "claude-code",
                "sl_id": "sl-offered-1",
                "source": "read",
                "offered": True,
            },
            {
                "ts": "2026-07-01T00:01:10Z",
                "session_id": "s-offered",
                "kind": "applied",
                "tool": "claude-code",
                "slice_id": "sl-offered-1",
            },
            {
                "ts": "2026-07-01T00:01:20Z",
                "session_id": "s-copilot",
                "tool": "copilot-cli",
                "sl_id": "sl-offered-3",
                "source": "read",
                "offered": True,
            },
            {
                "ts": "2026-07-01T00:01:30Z",
                "session_id": "s-direct",
                "tool": "claude-code",
                "sl_id": "sl-direct",
                "source": "read",
            },
        ],
    )

    # usage funnel must be installed and emit the expected contract dimensions:
    # session citation, unique-slice coverage, offered-to-read conversion, and explicit applied.
    assert (
        cli.main(
            ["usage", "funnel", "--memory-root", str(tmp_path), "--json"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["summary"]["sessions_offered"] == 2
    assert report["summary"]["sessions_with_read"] == 2
    assert report["summary"]["read_through_rate"] == 100.0
    assert report["summary"]["sessions_with_applied"] == 1
    assert report["summary"]["applied_rate"] == 50.0
    assert len(report["top_slices"]) == 2
    assert any(item["slice_id"] == "sl-offered-1" for item in report["top_slices"])
    assert any(item["slice_id"] == "sl-offered-3" for item in report["top_slices"])
    assert report["read_attribution"] == {
        "offer_then_read_events": 2,
        "offer_then_read_sessions": 2,
        "direct_read_events": 1,
        "direct_read_sessions": 1,
        "by_tool": {
            "claude-code": {
                "offer_then_read_events": 1,
                "offer_then_read_sessions": 1,
                "direct_read_events": 1,
                "direct_read_sessions": 1,
            },
            "copilot-cli": {
                "offer_then_read_events": 1,
                "offer_then_read_sessions": 1,
                "direct_read_events": 0,
                "direct_read_sessions": 0,
            },
        },
    }
    assert report["by_tool"]["claude-code"]["sessions_with_applied"] == 1
    assert report["by_tool"]["copilot-cli"]["sessions_with_read"] == 1
    assert report["by_tool"]["copilot-cli"]["applied_rate"] == 0.0

    matrix = Path("docs/cross-cli-capability-matrix.md").read_text(encoding="utf-8")
    assert "Task 8 填" not in matrix
