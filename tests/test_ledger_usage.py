# tests/test_ledger_usage.py
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.ledger import usage as usage_ledger


class IterLedgerEventsStreamingTests(unittest.TestCase):
    """v5 BLOCKER #1: no ``Path.read_text().splitlines()``, no whole-file list."""

    def test_never_calls_path_read_text(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
            with mock.patch.object(
                Path, "read_text", side_effect=AssertionError("must not call Path.read_text")
            ):
                diag = usage_ledger.new_diagnostics()
                events = list(usage_ledger.iter_ledger_events(path, diag))
            self.assertEqual(events, [{"a": 1}, {"b": 2}])
            self.assertEqual(diag, usage_ledger.new_diagnostics())

    def test_large_ledger_streams_every_line(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.jsonl"
            n = 20000
            with path.open("w", encoding="utf-8") as fh:
                for i in range(n):
                    fh.write(json.dumps({"i": i}) + "\n")
            diag = usage_ledger.new_diagnostics()
            count = 0
            for event in usage_ledger.iter_ledger_events(path, diag):
                self.assertEqual(event["i"], count)
                count += 1
            self.assertEqual(count, n)
            self.assertEqual(diag, usage_ledger.new_diagnostics())

    def test_missing_file_yields_nothing(self):
        with TemporaryDirectory() as tmp:
            diag = usage_ledger.new_diagnostics()
            events = list(usage_ledger.iter_ledger_events(Path(tmp) / "absent.jsonl", diag))
            self.assertEqual(events, [])
            self.assertEqual(diag, usage_ledger.new_diagnostics())


class IterLedgerEventsFailSoftTests(unittest.TestCase):
    """v5 BLOCKER #2/#4: malformed/non-object lines and I/O errors are fail-soft
    and bounded, and reads never mutate the ledger."""

    def test_malformed_and_non_object_lines_are_bounded_and_do_not_abort(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            original = '{"ok":1}\nnot-json\n[1,2,3]\n\n{"ok":2}\n'
            path.write_text(original, encoding="utf-8")
            diag = usage_ledger.new_diagnostics()
            events = list(usage_ledger.iter_ledger_events(path, diag))
            self.assertEqual(events, [{"ok": 1}, {"ok": 2}])
            self.assertEqual(diag["json_decode_error"], 1)
            self.assertEqual(diag["non_object"], 1)
            self.assertEqual(set(diag.keys()), set(usage_ledger.DIAG_KEYS))
            # read-only: ledger content is untouched
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_open_oserror_is_fail_soft_and_does_not_mutate_ledger(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('{"ok":1}\n', encoding="utf-8")
            diag = usage_ledger.new_diagnostics()
            with mock.patch.object(Path, "open", side_effect=OSError("boom")):
                events = list(usage_ledger.iter_ledger_events(path, diag))
            self.assertEqual(events, [])
            self.assertEqual(path.read_text(encoding="utf-8"), '{"ok":1}\n')

    def test_invalid_utf8_mid_file_is_fail_soft(self):
        # A UnicodeDecodeError anywhere in the file must not raise out of
        # iter_ledger_events (fail-soft); Python's buffered text reader may
        # need to look ahead past a valid line to find the bad bytes, so the
        # exact partial-recovery boundary isn't guaranteed — only that the
        # generator terminates cleanly instead of propagating the exception.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            with path.open("wb") as fh:
                fh.write(b'{"ok":1}\n')
                fh.write(b"\xff\xfe not valid utf-8\n")
            diag = usage_ledger.new_diagnostics()
            events = list(usage_ledger.iter_ledger_events(path, diag))
            self.assertIn(events, ([], [{"ok": 1}]))


class CollectUsageReadsTests(unittest.TestCase):
    """v5 requirement #2/#3: identity+window matched read_count/last_read_at
    aggregation with bounded diagnostics for every exclusion reason."""

    def test_bounded_diagnostics_and_valid_attribution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)

            offered = [
                {"tool": "claude-code", "session_id": "s1", "ts": "2026-07-20T00:00:00Z",
                 "offered": [{"sl_id": "sl-a"}]},
                {"tool": "(unknown)", "session_id": "s2", "ts": "2026-07-20T00:00:00Z",
                 "offered": [{"sl_id": "sl-b"}]},
            ]
            (led / "offered.jsonl").write_text(
                "\n".join(json.dumps(e) for e in offered) + "\n", encoding="utf-8"
            )
            usage = [
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"},  # valid match
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-21T01:00:00Z", "kind": "applied"},  # excluded: applied
                {"tool": "(unknown)", "session_id": "s2", "sl_id": "sl-b", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"},  # missing_tool
                {"tool": "claude-code", "session_id": "", "sl_id": "sl-c", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"},  # missing_session
                {"tool": "claude-code", "session_id": "s1", "sl_id": "", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"},  # missing_sl_id
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "not-a-timestamp"},  # invalid_ts
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-28T00:00:00Z"},  # future_event
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-01-01T00:00:00Z"},  # window_older
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-z", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"},  # read_without_offered
            ]
            (led / "memory_usage.jsonl").write_text(
                "\n".join(json.dumps(e) for e in usage) + "\n", encoding="utf-8"
            )

            result, diag = usage_ledger.collect_usage_reads(root, now)

            self.assertEqual(result, {"sl-a": (1, "2026-07-21T00:00:00Z")})
            expected_diag = usage_ledger.new_diagnostics()
            expected_diag.update({
                "missing_tool": 2,
                "missing_session": 1,
                "missing_sl_id": 1,
                "invalid_ts": 1,
                "future_event": 1,
                "window_older": 1,
                "read_without_offered": 1,
            })
            self.assertEqual(diag, expected_diag)

    def test_does_not_mutate_ledger_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            offered_content = json.dumps(
                {"tool": "claude-code", "session_id": "s1", "ts": "2026-07-20T00:00:00Z",
                 "offered": [{"sl_id": "sl-a"}]}
            ) + "\n"
            usage_content = json.dumps(
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-21T00:00:00Z"}
            ) + "\n"
            (led / "offered.jsonl").write_text(offered_content, encoding="utf-8")
            (led / "memory_usage.jsonl").write_text(usage_content, encoding="utf-8")

            usage_ledger.collect_usage_reads(root, datetime(2026, 7, 27, tzinfo=timezone.utc))

            self.assertEqual((led / "offered.jsonl").read_text(encoding="utf-8"), offered_content)
            self.assertEqual((led / "memory_usage.jsonl").read_text(encoding="utf-8"), usage_content)

    def test_multiple_reads_aggregate_count_and_latest_last_read_at(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            (led / "offered.jsonl").write_text(
                json.dumps({"tool": "codex", "session_id": "s1", "ts": "2026-07-01T00:00:00Z",
                            "offered": [{"sl_id": "sl-a"}]}) + "\n",
                encoding="utf-8",
            )
            reads = [
                {"tool": "codex", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-02T00:00:00Z"},
                {"tool": "codex", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-05T00:00:00Z"},
                {"tool": "codex", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                 "ts": "2026-07-03T00:00:00Z"},
            ]
            (led / "memory_usage.jsonl").write_text(
                "\n".join(json.dumps(e) for e in reads) + "\n", encoding="utf-8"
            )
            result, _diag = usage_ledger.collect_usage_reads(
                root, datetime(2026, 7, 27, tzinfo=timezone.utc)
            )
            self.assertEqual(result["sl-a"], (3, "2026-07-05T00:00:00Z"))


class UsageBoostFormulaTests(unittest.TestCase):
    def test_zero_read_count_has_zero_boost(self):
        self.assertEqual(usage_ledger.usage_boost(0), 0.0)

    def test_boost_grows_with_log2_and_caps_at_0_04(self):
        self.assertAlmostEqual(usage_ledger.usage_boost(1), 0.01)
        self.assertLess(usage_ledger.usage_boost(1), usage_ledger.usage_boost(3))
        self.assertEqual(usage_ledger.usage_boost(10_000_000), 0.04)


if __name__ == "__main__":
    unittest.main()
