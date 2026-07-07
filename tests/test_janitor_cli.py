from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli


_OLD_RECORD = """---
memory_layer: knowledge
slice_id: sl-1
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: a.md
captured_at: "2020-01-01T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
body
"""


class JanitorCliTests(unittest.TestCase):
    def test_scan_dry_run_prints_summary_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            kroot.mkdir(parents=True)
            (kroot / "sl-1.md").write_text(_OLD_RECORD, encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["janitor", "scan",
                    "--memory-root", str(root),
                    "--knowledge-root", str(kroot),
                    "--now", "2026-05-31T00:00:00Z",
                    "--dry-run",
                ])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["summary"]["decayed"], 1)
            self.assertFalse((root / "runtime" / "ledger" / "lifecycle.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
