"""Test that moc warnings propagate through dream/cli.py to orchestrator status."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from paulsha_hippo import cli


def _seed_simple(root: Path):
    """Create a simple slice for processing."""
    raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("""---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-06-02T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha
""", encoding="utf-8")


class DreamCliMocWarningsTest(unittest.TestCase):
    def test_moc_warnings_propagate_to_orchestrator_status(self):
        """Moc warnings from moc_runner should cause dream status=partial."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_simple(root)
            
            # Mock atomize and janitor to succeed
            mock_atomize_result = {"summary": {"skipped": 0}, "warnings": []}
            mock_janitor_result = {"summary": {"skipped": 0}, "warnings": []}
            
            # Mock moc_runner to return warnings (the current bug)
            mock_moc_result = {
                "renamed": True,
                "linked": 0,
                "mocs": True,
                "faceout": True,
                "indexed": True,
                "warnings": ["linker degraded: test"]
            }
            
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.atomizer.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaa"*10),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbb"*10),
            ), patch(
                "paulsha_hippo.dream.cli.atomizer_pipeline.run",
                return_value=mock_atomize_result,
            ), patch(
                "paulsha_hippo.dream.cli.janitor_scanner.run_scan",
                return_value=mock_janitor_result,
            ), patch(
                "paulsha_hippo.moc.runner.run_moc",
                return_value=mock_moc_result,
            ), redirect_stdout(buf):
                rc = cli.main(["dream", "run",
                    "--memory-root", str(root),
                    "--now", "2026-06-02T05:00:00Z",
                ])
            
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            
            # The key assertion: moc warnings should cause partial status
            # Currently fails because dream/cli.py wraps moc result as
            # {"summary": ..., "warnings": []} which hides actual warnings
            self.assertEqual(payload["status"], "partial", 
                           f"Moc warnings should produce partial status, got {payload['status']}\n" +
                           f"Full payload: {json.dumps(payload, indent=2)}")
            self.assertIn("moc", payload["passes"])

    def test_intentionally_excluded_produced_slice_is_not_missing(self):
        """Review records are produced, but intentionally stay outside retrieval."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_simple(root)
            mock_moc_result = {
                "renamed": True,
                "linked": 0,
                "mocs": True,
                "faceout": True,
                "indexed": True,
                "warnings": [],
                "index_coverage": {"scanned": 2},
            }
            audit = SimpleNamespace(searchable_ids={"sl-knowledge"})
            reconciliation = SimpleNamespace(
                ok=True,
                problems=[],
                eligible_ids={"sl-knowledge"},
                indexed_ids={"sl-knowledge"},
            )

            buf = io.StringIO()
            with patch(
                "paulsha_hippo.atomizer.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaa" * 10),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbb" * 10),
            ), patch(
                "paulsha_hippo.dream.cli.atomizer_pipeline.run",
                return_value={
                    "summary": {"skipped": 0},
                    "warnings": [],
                    "produced_slice_ids": ["sl-knowledge", "sl-review"],
                },
            ), patch(
                "paulsha_hippo.dream.cli.janitor_scanner.run_scan",
                return_value={"summary": {"skipped": 0}, "warnings": []},
            ), patch(
                "paulsha_hippo.moc.runner.run_moc", return_value=mock_moc_result,
            ), patch(
                "paulsha_hippo.moc.census.audit_indexed_ids", return_value=audit,
            ), patch(
                "paulsha_hippo.moc.census.reconcile_index", return_value=reconciliation,
            ), redirect_stdout(buf):
                rc = cli.main([
                    "dream", "run", "--memory-root", str(root),
                    "--now", "2026-06-02T05:00:00Z",
                ])

            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            summary = payload["passes"]["moc"]
            self.assertEqual(summary["produced_eligible"], 1)
            self.assertEqual(summary["produced_excluded"], 1)
            self.assertEqual(summary["metadata_indexed"], 1)
            self.assertEqual(summary["fts_indexed"], 1)
            self.assertNotIn("warnings", payload["passes"]["moc"])


if __name__ == "__main__":
    unittest.main()
