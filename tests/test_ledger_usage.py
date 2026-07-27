# tests/test_ledger_usage.py
from __future__ import annotations

import gc
import json
import tracemalloc
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

    def test_exists_oserror_is_fail_soft(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            diag = usage_ledger.new_diagnostics()
            with mock.patch.object(Path, "exists", side_effect=OSError("boom")):
                self.assertEqual(
                    list(usage_ledger.iter_ledger_events(path, diag)),
                    [],
                )

    def test_invalid_utf8_mid_file_is_fail_soft(self):
        # Decode must be per physical line: one poisoned row cannot make the
        # text buffer discard valid rows before or after it.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            with path.open("wb") as fh:
                fh.write(b'{"ok":1}\n')
                fh.write(b"\xff\xfe not valid utf-8\n")
                fh.write(b'{"ok":2}\n')
            diag = usage_ledger.new_diagnostics()
            events = list(usage_ledger.iter_ledger_events(path, diag))
            self.assertEqual(events, [{"ok": 1}, {"ok": 2}])
            self.assertEqual(diag["utf8_decode_error"], 1)


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

            result, diag, stats = usage_ledger.collect_usage_reads(root, now)

            # sl-z was read but never offered: issue #67 direct read -> count 0,
            # last_read_at present so janitor retention still sees it.
            self.assertEqual(result, {
                "sl-a": (1, "2026-07-21T00:00:00Z"),
                "sl-z": (0, "2026-07-21T00:00:00Z"),
            })
            expected_diag = usage_ledger.new_diagnostics()
            expected_diag.update({
                "missing_tool": 2,
                "missing_session": 1,
                "missing_sl_id": 1,
                "invalid_ts": 1,
                "future_event": 1,
                "window_older": 1,
            })
            self.assertEqual(diag, expected_diag)
            self.assertEqual(stats, {"direct_read": 1})

    def test_unknown_is_rejected_for_every_identity_component(self):
        cases = (
            ("(unknown)", "s1", "sl-a"),
            ("codex", "(unknown)", "sl-a"),
            ("codex", "s1", "(unknown)"),
        )
        for tool, session_id, slice_id in cases:
            with self.subTest(tool=tool, session_id=session_id, slice_id=slice_id):
                self.assertFalse(
                    usage_ledger.valid_identity(tool, session_id, slice_id)
                )

    def test_out_of_window_and_future_offers_never_cross_match(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)
            offers = [
                {
                    "tool": "codex",
                    "session_id": "old",
                    "ts": "2026-01-01T00:00:00Z",
                    "offered": [{"sl_id": "sl-old"}],
                },
                {
                    "tool": "codex",
                    "session_id": "future",
                    "ts": "2026-07-28T00:00:00Z",
                    "offered": [{"sl_id": "sl-future"}],
                },
            ]
            reads = [
                {
                    "tool": "codex",
                    "session_id": "old",
                    "sl_id": "sl-old",
                    "source": "read",
                    "ts": "2026-07-21T00:00:00Z",
                },
                {
                    "tool": "codex",
                    "session_id": "future",
                    "sl_id": "sl-future",
                    "source": "read",
                    "ts": "2026-07-21T00:00:00Z",
                },
            ]
            (led / "offered.jsonl").write_text(
                "\n".join(json.dumps(event) for event in offers) + "\n",
                encoding="utf-8",
            )
            (led / "memory_usage.jsonl").write_text(
                "\n".join(json.dumps(event) for event in reads) + "\n",
                encoding="utf-8",
            )

            result, diag, stats = usage_ledger.collect_usage_reads(root, now)

            # The out-of-window / future offers must not attribute either read:
            # read_count stays 0 on both. Issue #67: the reads themselves are
            # valid and in-window, so they land as direct reads rather than
            # vanishing.
            self.assertEqual(result, {
                "sl-old": (0, "2026-07-21T00:00:00Z"),
                "sl-future": (0, "2026-07-21T00:00:00Z"),
            })
            self.assertEqual(diag["window_older"], 1)
            self.assertEqual(diag["future_event"], 1)
            self.assertEqual(stats["direct_read"], 2)

    def test_repeated_offers_for_one_identity_use_bounded_memory(self):
        def peak_for(offer_count: int) -> int:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                led = root / "runtime" / "ledger"
                led.mkdir(parents=True)
                event = {
                    "tool": "codex",
                    "session_id": "s1",
                    "ts": "2026-07-20T00:00:00Z",
                    "offered": [{"sl_id": "sl-a"}],
                }
                with (led / "offered.jsonl").open("w", encoding="utf-8") as handle:
                    for _ in range(offer_count):
                        handle.write(json.dumps(event) + "\n")
                (led / "memory_usage.jsonl").write_text("", encoding="utf-8")
                gc.collect()
                tracemalloc.start()
                usage_ledger.collect_usage_reads(
                    root, datetime(2026, 7, 27, tzinfo=timezone.utc)
                )
                _current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                return peak

        small_peak = peak_for(100)
        large_peak = peak_for(20_000)
        self.assertLess(
            large_peak,
            small_peak + 200_000,
            (small_peak, large_peak),
        )

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
            result, _diag, _stats = usage_ledger.collect_usage_reads(
                root, datetime(2026, 7, 27, tzinfo=timezone.utc)
            )
            self.assertEqual(result["sl-a"], (3, "2026-07-05T00:00:00Z"))


class DirectReadAttributionTests(unittest.TestCase):
    """Issue #67: an un-offered ("direct") read is a normal category, not a diagnostic.

    It must feed the janitor retention base (``last_read_at``) — a read is
    evidence the slice is alive — while leaving the search usage boost
    (``read_count``) to offer-attributed reads only, so the funnel's
    conversion-rate semantics stay intact.
    """

    @staticmethod
    def _write(root: Path, offers: list[dict], reads: list[dict]) -> None:
        led = root / "runtime" / "ledger"
        led.mkdir(parents=True, exist_ok=True)
        (led / "offered.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in offers), encoding="utf-8"
        )
        (led / "memory_usage.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in reads), encoding="utf-8"
        )

    def test_read_without_offered_is_not_a_diagnostic_key(self):
        self.assertNotIn("read_without_offered", usage_ledger.DIAG_KEYS)
        self.assertNotIn("read_without_offered", usage_ledger.new_diagnostics())

    def test_direct_read_yields_last_read_with_zero_read_count(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)
            self._write(root, [], [
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-direct",
                 "source": "read", "ts": "2026-07-21T00:00:00Z"},
            ])

            result, diag, stats = usage_ledger.collect_usage_reads(root, now)

            # read_count 0 -> zero boost; last_read_at present -> retention sees it.
            self.assertEqual(result, {"sl-direct": (0, "2026-07-21T00:00:00Z")})
            self.assertEqual(usage_ledger.usage_boost(result["sl-direct"][0]), 0.0)
            self.assertEqual(diag, usage_ledger.new_diagnostics())
            self.assertEqual(stats["direct_read"], 1)

    def test_direct_reads_never_inflate_the_offer_attributed_read_count(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)
            self._write(
                root,
                [{"tool": "claude-code", "session_id": "s1", "ts": "2026-07-20T00:00:00Z",
                  "offered": [{"sl_id": "sl-a"}]}],
                [
                    {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
                     "source": "read", "ts": "2026-07-21T00:00:00Z"},
                    # same slice, different session, never offered there -> direct
                    {"tool": "claude-code", "session_id": "s2", "sl_id": "sl-a",
                     "source": "read", "ts": "2026-07-22T00:00:00Z"},
                ],
            )

            result, _diag, stats = usage_ledger.collect_usage_reads(root, now)

            # count stays 1 (offer-attributed only) but last_read advances to the
            # later direct read, so retention uses the freshest evidence.
            self.assertEqual(result, {"sl-a": (1, "2026-07-22T00:00:00Z")})
            self.assertEqual(stats["direct_read"], 1)

    def test_offer_not_strictly_before_read_counts_as_direct(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)
            self._write(
                root,
                [{"tool": "codex", "session_id": "s1", "ts": "2026-07-21T00:00:00Z",
                  "offered": [{"sl_id": "sl-a"}]}],
                [{"tool": "codex", "session_id": "s1", "sl_id": "sl-a", "source": "read",
                  "ts": "2026-07-21T00:00:00Z"}],
            )

            result, diag, stats = usage_ledger.collect_usage_reads(root, now)

            self.assertEqual(result, {"sl-a": (0, "2026-07-21T00:00:00Z")})
            self.assertEqual(diag, usage_ledger.new_diagnostics())
            self.assertEqual(stats["direct_read"], 1)

    def test_malformed_and_out_of_window_reads_are_still_diagnostics_not_direct(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 27, tzinfo=timezone.utc)
            self._write(root, [], [
                {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
                 "source": "read", "ts": "2026-01-01T00:00:00Z"},   # window_older
                {"tool": "(unknown)", "session_id": "s1", "sl_id": "sl-a",
                 "source": "read", "ts": "2026-07-21T00:00:00Z"},   # missing_tool
            ])

            result, diag, stats = usage_ledger.collect_usage_reads(root, now)

            self.assertEqual(result, {})
            self.assertEqual(diag["window_older"], 1)
            self.assertEqual(diag["missing_tool"], 1)
            self.assertEqual(stats["direct_read"], 0)


class UsageBoostFormulaTests(unittest.TestCase):
    def test_zero_read_count_has_zero_boost(self):
        self.assertEqual(usage_ledger.usage_boost(0), 0.0)

    def test_boost_grows_with_log2_and_caps_at_0_04(self):
        self.assertAlmostEqual(usage_ledger.usage_boost(1), 0.01)
        self.assertLess(usage_ledger.usage_boost(1), usage_ledger.usage_boost(3))
        self.assertEqual(usage_ledger.usage_boost(10_000_000), 0.04)


if __name__ == "__main__":
    unittest.main()
