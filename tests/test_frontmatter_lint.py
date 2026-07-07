import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paulsha_hippo.lint.frontmatter_lint import validate_file


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontmatterLintTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def write_doc(self, text):
        path = self.root / "doc.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_lint_accepts_required_stage2_frontmatter(self):
        doc = self.write_doc("""---
memory_layer: inbox
project: paulshaclaw
source_agent: copilot-cli
source_session: abc-123
source_artifact: session
captured_at: 2026-05-24T00:00:00+00:00
provenance:
  repo: hamanpaul/paulshaclaw
  commit: e300b08
  path: tests/session.json
---
body
""")
        self.assertEqual(validate_file(doc), [])

    def test_lint_rejects_missing_nested_provenance_path(self):
        doc = self.write_doc("""---
memory_layer: inbox
project: paulshaclaw
source_agent: copilot-cli
source_session: abc-123
source_artifact: session
captured_at: 2026-05-24T00:00:00+00:00
provenance:
  repo: hamanpaul/paulshaclaw
  commit: e300b08
---
body
""")
        self.assertIn(
            "missing required frontmatter field: provenance.path",
            validate_file(doc),
        )

    def test_lint_rejects_empty_required_project(self):
        doc = self.write_doc("""---
memory_layer: inbox
project:
source_agent: copilot-cli
source_session: abc-123
source_artifact: session
captured_at: 2026-05-24T00:00:00+00:00
provenance:
  repo: hamanpaul/paulshaclaw
  commit: e300b08
  path: tests/session.json
---
body
""")
        self.assertIn(
            "empty required frontmatter field: project",
            validate_file(doc),
        )

    def test_lint_rejects_empty_required_nested_provenance_path(self):
        doc = self.write_doc("""---
memory_layer: inbox
project: paulshaclaw
source_agent: copilot-cli
source_session: abc-123
source_artifact: session
captured_at: 2026-05-24T00:00:00+00:00
provenance:
  repo: hamanpaul/paulshaclaw
  commit: e300b08
  path:
---
body
""")
        self.assertIn(
            "empty required frontmatter field: provenance.path",
            validate_file(doc),
        )

    def test_cli_exits_one_for_validation_errors(self):
        doc = self.write_doc("""---
memory_layer: inbox
project: paulshaclaw
source_agent: copilot-cli
source_session: abc-123
source_artifact: session
captured_at: 2026-05-24T00:00:00+00:00
provenance:
  repo: hamanpaul/paulshaclaw
  commit: e300b08
---
body
""")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "paulsha_hippo.lint.frontmatter_lint",
                str(doc),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("missing required frontmatter field: provenance.path", completed.stderr)


if __name__ == "__main__":
    unittest.main()
