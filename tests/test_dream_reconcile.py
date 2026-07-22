"""Tests for dream reconcile: _slices ↔ processing ledger reconciliation."""
from __future__ import annotations

import fcntl
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.dream import reconcile


def _write_fragment(slices_dir: Path, agent: str, session: str, index: int = 0) -> Path:
    frag = slices_dir / f"{agent}__{session}__{index:03d}.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text(
        f"---\nmemory_layer: inbox\nproject: proj\n"
        f"source_agent: {agent}\nsource_session: {session}\n"
        f"source_artifact: session\ncaptured_at: 2026-07-15T03:00:00\n"
        f"session_title: \"test\"\nprovenance:\n  repo: ''\n  commit: ''\n  path: ''\n"
        f"fragment_index: {index}\nparent_session_ref: {agent}:{session}\n---\n\nbody\n"
    )
    return frag


class TestReconcileDryRun(unittest.TestCase):
    """8.3: dry-run classifies fragments vs ledger states."""

    def test_empty_slices_dir(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["orphan_fragment"], 0)
        self.assertEqual(data["summary"]["terminal_unarchived"], 0)
        self.assertEqual(data["summary"]["stale_split"], 0)
        self.assertEqual(data["summary"]["healthy"], 0)

    def test_orphan_fragment(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "abc123")
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["orphan_fragment"], 1)
        self.assertEqual(len(data["details"]), 1)
        self.assertEqual(data["details"][0]["category"], "orphan_fragment")
        self.assertEqual(data["details"][0]["session_key"], "claude:abc123")

    def test_terminal_unarchived(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "abc123")
            from paulsha_hippo.ledger import processing
            processing.append_state(
                memory_root, session_key="claude:abc123", state="promoted",
                now="2026-07-16T00:00:00", config_hash="abc12345",
            )
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["terminal_unarchived"], 1)

    def test_stale_split(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            from paulsha_hippo.ledger import processing
            processing.append_state(
                memory_root, session_key="claude:abc123", state="split",
                now="2026-07-15T00:00:00", config_hash="abc12345",
                fragments=3,
            )
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["stale_split"], 1)

    def test_healthy(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "abc123")
            from paulsha_hippo.ledger import processing
            processing.append_state(
                memory_root, session_key="claude:abc123", state="split",
                now="2026-07-15T00:00:00", config_hash="abc12345",
                fragments=1,
            )
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["healthy"], 1)

    def test_malformed_fragment(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            slices_dir.mkdir(parents=True)
            frag = slices_dir / "claude__abc__000.md"
            frag.write_text("not valid frontmatter\nno --- here\n")
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
        data = json.loads(result)
        self.assertEqual(data["summary"]["malformed"], 1)

    def test_policy_rejected_fragment_is_malformed(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "secret")
            with mock.patch.object(
                reconcile.policy,
                "check_boundary",
                return_value=mock.Mock(hits=(object(),)),
            ) as check_boundary:
                result = reconcile.run_reconcile(
                    memory_root, now="2026-07-21T00:00:00", dry_run=True,
                )

        data = json.loads(result)
        self.assertEqual(data["summary"]["malformed"], 1)
        check_boundary.assert_called_once()
        self.assertEqual(check_boundary.call_args.args[0], "external_to_raw")
        self.assertEqual(check_boundary.call_args.kwargs["session_ref"], "claude:secret")


class TestReconcileApply(unittest.TestCase):
    """8.4: apply fixes orphan/terminal/stale sessions."""

    def test_apply_orphan_sets_split(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "abc123")
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", apply=True,
            )
            data = json.loads(result)
            self.assertEqual(data["applied"]["applied"], 1)
            from paulsha_hippo.ledger import processing
            events = processing.fold_events(memory_root)
            self.assertEqual(events["claude:abc123"]["state"], "split")

    def test_apply_terminal_archives_fragments(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            frag = _write_fragment(slices_dir, "claude", "abc123")
            from paulsha_hippo.ledger import processing
            processing.append_state(
                memory_root, session_key="claude:abc123", state="promoted",
                now="2026-07-16T00:00:00", config_hash="abc12345",
            )
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", apply=True,
            )
            data = json.loads(result)
            self.assertEqual(data["applied"]["categories"]["terminal_unarchived"], 1)
            self.assertFalse(frag.exists())
            archive_path = (
                memory_root / "archive" / "fragments" / "2026-07"
                / "claude__abc123__000.md"
            )
            self.assertTrue(archive_path.exists())

    def test_apply_stale_split_marks_no_findings(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            from paulsha_hippo.ledger import processing
            processing.append_state(
                memory_root, session_key="claude:abc123", state="split",
                now="2026-07-15T00:00:00", config_hash="abc12345", fragments=3,
            )
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", apply=True,
            )
            data = json.loads(result)
            self.assertEqual(data["applied"]["categories"]["stale_split"], 1)
            events = processing.fold_events(memory_root)
            self.assertEqual(events["claude:abc123"]["state"], "no-findings")

    def test_apply_limit_n(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            for i in range(5):
                _write_fragment(slices_dir, "claude", f"sess{i}")
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", apply=True, limit=2,
            )
            data = json.loads(result)
            self.assertEqual(data["applied"]["categories"]["orphan_fragment"], 2)

    def test_apply_multiple_orphans_all_processed(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            slices_dir = memory_root / "inbox" / "_slices" / "proj"
            _write_fragment(slices_dir, "claude", "good")
            _write_fragment(slices_dir, "claude", "bad")
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", apply=True,
            )
            data = json.loads(result)
            self.assertEqual(data["applied"]["applied"], 2)
            self.assertEqual(data["applied"]["errors"], 0)


class TestReconcileDreamLock(unittest.TestCase):
    """8.4: reconcile must hold dream singleton lock."""

    def test_lock_held_skips(self):
        with TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir)
            lock_path = memory_root / "runtime" / "locks" / "dream.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = open(lock_path, "w")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = reconcile.run_reconcile(
                memory_root, now="2026-07-21T00:00:00", dry_run=True,
            )
            data = json.loads(result)
            self.assertIn("skipped", data)
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
