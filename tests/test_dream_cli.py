from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from paulsha_hippo import cli
from paulsha_hippo.ledger import dream

_RAW = """---
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
"""


def _seed(root: Path):
    raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(_RAW, encoding="utf-8")


class DreamCliTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--dry-run",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("dry_run"))
            self.assertIn("passes", payload)
            self.assertIn("atomize", payload["passes"])
            self.assertIn("janitor", payload["passes"])
            self.assertIsNone(dream.last_run(root))

    def test_require_idle_busy_skips(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ), patch(
                "paulsha_hippo.dream.cli.idle.is_idle",
                return_value=False,
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--require-idle",
                        "--max-load",
                        "-1",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("skipped"), "system busy")
            self.assertEqual(payload.get("backlog_depth"), 1)
            self.assertIsNone(dream.last_run(root))

    def test_status_reports_backlog(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["dream", "status", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["backlog_depth"], 1)


if __name__ == "__main__":
    unittest.main()
