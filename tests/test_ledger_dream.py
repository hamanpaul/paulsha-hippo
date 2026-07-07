"""
Tests for dream ledger (Stage 2 Task 1).
"""
import json
import os
from unittest import mock
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import dream


class TestDreamLedger(unittest.TestCase):
    def test_dream_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            expected = root / "runtime" / "ledger" / "dream.jsonl"
            self.assertEqual(dream.dream_path(root), expected)

    def test_append_and_read_runs(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            r1 = {"ts": "2025-01-01T00:00:00Z", "run_id": "r1", "payload": {"a": 1}}
            r2 = {"ts": "2025-01-01T00:00:01Z", "run_id": "r2", "payload": {"b": 2}}

            dream.append_run(root, r1)
            dream.append_run(root, r2)

            runs = dream.read_runs(root)
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0], r1)
            self.assertEqual(runs[1], r2)

    def test_last_run(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.assertIsNone(dream.last_run(root))
            r = {"ts": "2025-01-01T00:00:00Z", "run_id": "r1"}
            dream.append_run(root, r)
            self.assertEqual(dream.last_run(root), r)

    def test_read_missing_returns_empty(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.assertEqual(dream.read_runs(root), [])

    def test_corrupt_line_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = dream.dream_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write(json.dumps({"ts": "2025-01-01T00:00:00Z", "run_id": "r1"}) + "\n")
                f.write("not a json\n")

            with self.assertRaises(dream.DreamLedgerError) as ctx:
                dream.read_runs(root)

            self.assertIn("line", str(ctx.exception).lower())

    def test_non_dict_json_line_fails(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = dream.dream_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write(json.dumps({"ts": "2025-01-01T00:00:00Z", "run_id": "r1"}) + "\n")
                f.write("[]\n")

            with self.assertRaises(dream.DreamLedgerError):
                dream.read_runs(root)

    def test_append_run_rejects_non_dict(self):
        """append_run should reject non-dict records at write time."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # common bad payloads: list, None, int, string
            for bad in ([], None, 123, "hi"):
                with self.assertRaises(TypeError):
                    dream.append_run(root, bad)

    def test_backlog_depth_counts_markdown_excluding_slices(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inbox = root / "inbox"
            (inbox / "a.md").parent.mkdir(parents=True, exist_ok=True)
            (inbox / "a.md").write_text("1")
            (inbox / "sub" / "b.md").parent.mkdir(parents=True, exist_ok=True)
            (inbox / "sub" / "b.md").write_text("2")
            (inbox / "_slices" / "s1" / "c.md").parent.mkdir(parents=True, exist_ok=True)
            (inbox / "_slices" / "s1" / "c.md").write_text("3")

            self.assertEqual(dream.backlog_depth(root), 2)

    def test_append_run_fsyncs(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            r = {"ts": "2025-01-01T00:00:00Z", "run_id": "r1"}
            with mock.patch("os.fsync") as fsync:
                dream.append_run(root, r)
            fsync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
