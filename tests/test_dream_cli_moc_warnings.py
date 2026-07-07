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
                "paulsha_hippo.dream.cli.atomizer_config.load_config",
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


if __name__ == "__main__":
    unittest.main()
