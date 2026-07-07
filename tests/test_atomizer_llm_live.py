from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.lib.lifecycle.gate import run_static_gate_check_file
from paulsha_hippo import cli

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"


@unittest.skipUnless(
    os.environ.get("PSC_ATOMIZE_LIVE"),
    "set PSC_ATOMIZE_LIVE=1 to enable the real claude-gemma4 atomizer test",
)
class AtomizerLlmLiveTests(unittest.TestCase):
    def test_live_llm_atomize_produces_gate_valid_slice(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            override.write_text(f'known_projects_file: "{projects}"\n', encoding="utf-8")

            rc = cli.main(["atomize",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-06-02T03:00:00Z",
                    "--promoter",
                    "llm",
                    "--override",
                    str(override),
                ]
            )

            self.assertEqual(rc, 0)
            slice_paths = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1)
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
