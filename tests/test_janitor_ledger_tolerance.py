from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import config as janitor_config
from paulsha_hippo.janitor import scanner
from paulsha_hippo.ledger import retrieval_set


def _write_record(path: Path, slice_id: str, source_session: str) -> None:
    path.write_text(
        "---\n"
        "memory_layer: knowledge\n"
        f"slice_id: {slice_id}\n"
        "project: paulshaclaw\n"
        "source_agent: claude\n"
        f"source_session: {source_session}\n"
        "source_artifact: a.md\n"
        'captured_at: "2020-01-01T00:00:00Z"\n'
        "provenance:\n"
        "  repo: paulshaclaw\n"
        "  commit: c\n"
        "  path: docs/x.md\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )


class JanitorLedgerToleranceTests(unittest.TestCase):
    def test_mixed_import_jsonl_keeps_good_reactivation_lines(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir(parents=True)
            _write_record(knowledge_root / "sl-a.md", "sl-a", "sess-a")
            _write_record(knowledge_root / "sl-b.md", "sl-b", "sess-b")

            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            kwargs = dict(
                knowledge_root=knowledge_root,
                config=cfg,
                config_hash=cfg_hash,
                source_path_exists=lambda record: True,
            )

            scanner.run_scan(root, now="2026-05-31T00:00:00Z", **kwargs)

            import_path = root / "runtime" / "ledger" / "import.jsonl"
            import_path.parent.mkdir(parents=True, exist_ok=True)
            import_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "idempotency_key": "claude:sess-a",
                                "status": "updated",
                                "recorded_at": "2026-06-01T00:00:00Z",
                            }
                        ),
                        "",
                        "{bad json}",
                        json.dumps(
                            {
                                "idempotency_key": "claude:sess-b",
                                "status": "written",
                                "recorded_at": "2026-06-01T00:00:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = scanner.run_scan(root, now="2026-06-02T00:00:00Z", **kwargs)

            self.assertEqual(result["summary"]["reactivated"], 2)
            self.assertIn("skipped 2 bad line(s)", " ".join(result["warnings"]))
            self.assertEqual(
                retrieval_set.active_records(root, ["sl-a", "sl-b"]),
                ["sl-a", "sl-b"],
            )


class JanitorDirectReadWarningTests(unittest.TestCase):
    """Issue #67: un-offered reads must not pin ``hippo dream run`` at ``partial``.

    ``run_scan`` surfaces any non-zero usage diagnostic as a warning, and dream
    downgrades a run to ``partial`` whenever ``warnings_total > 0``. A direct
    read is normal agent behaviour, so it must not reach that warning at all —
    only genuine ledger defects may.
    """

    def _scan_with_reads(self, reads: list[dict], offers: list[dict] | None = None) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir(parents=True)
            _write_record(knowledge_root / "sl-a.md", "sl-a", "sess-a")
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True, exist_ok=True)
            (led / "offered.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in (offers or [])), encoding="utf-8"
            )
            (led / "memory_usage.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in reads), encoding="utf-8"
            )
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            # #70: usage diagnostics are windowed off the *janitor's own read
            # time* (usage_now), not the run's frozen `now`. These tests assert
            # fixed event timestamps against a fixed clock, so usage_now is
            # pinned explicitly here — otherwise, once the scanner's usage
            # clock default becomes real wall-clock time, these hard-coded
            # 2026-07-26 event timestamps would silently drift out of the
            # 30-day window (become `window_older`) as real time passes,
            # turning this into a time bomb rather than a deterministic test.
            return scanner.run_scan(
                root,
                now="2026-07-27T00:00:00Z",
                knowledge_root=knowledge_root,
                config=cfg,
                config_hash=cfg_hash,
                source_path_exists=lambda record: True,
                usage_now="2026-07-27T00:00:00Z",
            )

    def test_direct_reads_emit_no_usage_diagnostics_warning(self):
        result = self._scan_with_reads([
            {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
             "source": "read", "ts": "2026-07-26T00:00:00Z"},
        ])

        self.assertEqual(
            [w for w in result["warnings"] if "usage ledger diagnostics" in w], []
        )

    def test_genuine_ledger_defects_still_warn(self):
        result = self._scan_with_reads([
            {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
             "source": "read", "ts": "not-a-timestamp"},
        ])

        self.assertTrue(
            any("usage ledger diagnostics" in w and "invalid_ts" in w
                for w in result["warnings"]),
            result["warnings"],
        )


class JanitorUsageNowConcurrencyTests(unittest.TestCase):
    """Issue #70: a dream run freezes a single `now` at run-start and hands it
    to both the (slow) atomize pass and the janitor pass that runs after it.
    Any ledger event written concurrently — e.g. by another agent's prompt-time
    hook — while atomize is still running lands *after* that frozen `now`, but
    strictly *before* the janitor pass actually reads the ledger. Judging it
    against the frozen `now` misclassifies ordinary concurrent activity as
    `future_event`, which janitor escalates to a warning and which pins the
    whole dream run at `partial` (same anti-pattern as #64 / #67).

    `usage_now` decouples the usage-ledger clock from the run's frozen `now`:
    it defaults to the janitor's actual wall-clock read time, so concurrent
    writes are judged against *when they were actually read*, not against a
    timestamp fixed possibly 10+ minutes earlier at run-start.
    """

    def _scan(
        self, reads: list[dict], *, now: str, usage_now: str | None,
        pass_usage_now: bool = True,
    ) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir(parents=True)
            _write_record(knowledge_root / "sl-a.md", "sl-a", "sess-a")
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True, exist_ok=True)
            (led / "offered.jsonl").write_text("", encoding="utf-8")
            (led / "memory_usage.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in reads), encoding="utf-8"
            )
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            kwargs: dict = dict(
                knowledge_root=knowledge_root,
                config=cfg,
                config_hash=cfg_hash,
                now=now,
                source_path_exists=lambda record: True,
            )
            if pass_usage_now:
                kwargs["usage_now"] = usage_now
            return scanner.run_scan(root, **kwargs)

    def test_concurrent_write_after_frozen_now_but_before_usage_now_is_not_future_event(self):
        # Mirrors the real run: run_id frozen at 07:03:27Z; atomize takes ~13
        # minutes; janitor actually reads the ledger (usage_now) at 07:16:51Z.
        # An offered/read event written at 07:10:23Z — after the frozen `now`
        # but before janitor's actual read — must not be flagged.
        result = self._scan(
            [{"tool": "claude-code", "session_id": "9105547d", "sl_id": "sl-a",
              "source": "read", "ts": "2026-07-27T07:10:23Z"}],
            now="2026-07-27T07:03:27Z",
            usage_now="2026-07-27T07:16:51Z",
        )

        self.assertEqual(
            [w for w in result["warnings"] if "usage ledger diagnostics" in w], []
        )

    def test_genuine_clock_skew_beyond_usage_now_still_warns(self):
        # A real clock-skew event (postdates even the janitor's own read time)
        # must still be caught — the fix narrows the *false positive* window,
        # it must not blind the diagnostic altogether.
        result = self._scan(
            [{"tool": "claude-code", "session_id": "9105547d", "sl_id": "sl-a",
              "source": "read", "ts": "2026-07-27T08:00:00Z"}],
            now="2026-07-27T07:03:27Z",
            usage_now="2026-07-27T07:16:51Z",
        )

        self.assertTrue(
            any("usage ledger diagnostics" in w and "future_event" in w
                for w in result["warnings"]),
            result["warnings"],
        )

    def test_default_usage_now_uses_call_time_not_frozen_run_now(self):
        # Regression guard on the default itself: with no usage_now override
        # (how the real dream/janitor CLIs call run_scan), a concurrent write
        # timestamped at "real now" must not be future_event even though the
        # run's frozen `now` is far earlier — proving the usage clock default
        # is NOT simply `now`.
        near_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self._scan(
            [{"tool": "claude-code", "session_id": "9105547d", "sl_id": "sl-a",
              "source": "read", "ts": near_now}],
            now="2026-07-27T07:03:27Z",
            usage_now=None,
            pass_usage_now=False,
        )

        self.assertEqual(
            [w for w in result["warnings"] if "usage ledger diagnostics" in w], []
        )


if __name__ == "__main__":
    unittest.main()
