"""Tests for dream orchestrator (Stage 2 Task 4).

These tests assert the *plan-shaped* contract for Task 4.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.dream import orchestrator
from paulsha_hippo.ledger import dream


class TestDreamOrchestrator(unittest.TestCase):
    def test_runs_passes_in_order_and_persists_ok(self):
        calls: list[str] = []

        def atomize_fn():
            calls.append("a")
            return {"summary": {"skipped": 0}, "warnings": []}

        def janitor_fn():
            calls.append("j")
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            orchestrator.run_dream(
                root,
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-06-02T00:00:00Z",
                config_hash="cfg",
            )

            self.assertEqual(calls, ["a", "j"])
            record = dream.last_run(root)
            self.assertIsNotNone(record)
            self.assertEqual(record["run_id"], "dream-2026-06-02T00:00:00Z")
            self.assertEqual(record["passes"]["atomize"], {"skipped": 0})
            self.assertEqual(record["passes"]["janitor"], {"skipped": 0})
            self.assertEqual(record["status"], "ok")

    def test_janitor_runs_even_if_atomize_raises(self):
        calls: list[str] = []

        def atomize_fn():
            calls.append("a")
            raise RuntimeError("boom")

        def janitor_fn():
            calls.append("j")
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            orchestrator.run_dream(
                root,
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-06-02T00:00:00Z",
                config_hash="cfg",
            )

            self.assertEqual(calls, ["a", "j"])
            record = dream.last_run(root)
            self.assertEqual(
                record["passes"]["atomize"],
                {"error": "RuntimeError", "error_message": "boom", "errno": None},
            )
            self.assertEqual(record["errors"], ["atomize:RuntimeError"])

    def test_warnings_produce_partial(self):
        def atomize_fn():
            return {"summary": {"skipped": 0}, "warnings": ["warn"]}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            orchestrator.run_dream(
                root,
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-06-02T00:00:00Z",
                config_hash="cfg",
            )

            self.assertEqual(dream.last_run(root)["status"], "partial")

    def test_pass_warnings_are_recorded_in_dream_record(self):
        warning_text = "claude:s1: llm promote failed: x; session claude:s1 left in split"

        def atomize_fn():
            return {"summary": {"skipped": 1}, "warnings": [warning_text]}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            orchestrator.run_dream(
                root,
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-02T00:00:00Z",
                config_hash="cfg",
            )

            record = dream.last_run(root)
            self.assertEqual(record["status"], "partial")
            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["warnings"], [warning_text])
            self.assertEqual(atomize["warnings_total"], 1)
            self.assertEqual(record["passes"]["janitor"], {"skipped": 0})

    def test_pass_warnings_overflow_truncated_but_counted(self):
        all_warnings = [f"w{i}" for i in range(45)]

        def atomize_fn():
            return {"summary": {"skipped": 45}, "warnings": list(all_warnings)}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-02T00:00:00Z",
                config_hash="cfg",
            )

            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["warnings"], all_warnings[:10])
            self.assertEqual(atomize["warnings_total"], 45)

    def test_long_warning_strings_are_truncated(self):
        def atomize_fn():
            return {"summary": {"skipped": 1}, "warnings": ["x" * 2000]}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-02T00:00:00Z",
                config_hash="cfg",
            )

            self.assertEqual(record["passes"]["atomize"]["warnings"], ["x" * 500])

    def test_summary_dict_is_not_mutated_by_warning_recording(self):
        source_summary = {"skipped": 1}

        def atomize_fn():
            return {"summary": source_summary, "warnings": ["warn"]}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-02T00:00:00Z",
                config_hash="cfg",
            )

            self.assertNotIn("warnings", source_summary)
            self.assertNotIn("warnings_total", source_summary)

    def test_failure_record_keeps_bounded_error_message_and_errno(self):
        def atomize_fn():
            raise OSError(36, "File name too long: " + "x" * 600)

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-10T00:00:00Z",
                config_hash="cfg",
            )

            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["error"], "OSError")
            self.assertEqual(atomize["errno"], 36)
            self.assertIn("File name too long", atomize["error_message"])
            self.assertLessEqual(len(atomize["error_message"]), 500)
            self.assertEqual(record["errors"], ["atomize:OSError"])

    def test_errno_extracted_from_cause_chain(self):
        def atomize_fn():
            try:
                raise OSError(2, "No such file or directory")
            except OSError as exc:
                raise RuntimeError("wrapper") from exc

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-10T00:00:00Z",
                config_hash="cfg",
            )

            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["error"], "RuntimeError")
            self.assertEqual(atomize["errno"], 2)

    def test_dry_run_writes_no_ledger(self):
        def atomize_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            orchestrator.run_dream(
                root,
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-06-02T00:00:00Z",
                config_hash="cfg",
                dry_run=True,
            )

            self.assertIsNone(dream.last_run(root))

    def test_moc_warnings_produce_partial(self):
        def atomize_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        def moc_fn():
            return {"indexed": True, "renamed": True, "warnings": ["moc warn"]}

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            record = orchestrator.run_dream(
                root, atomize_fn=atomize_fn, janitor_fn=janitor_fn,
                moc_fn=moc_fn, now="2026-06-02T00:00:00Z", config_hash="cfg")

            self.assertEqual(record["status"], "partial")
            self.assertIn("indexed", record["passes"]["moc"])


if __name__ == "__main__":
    unittest.main()
