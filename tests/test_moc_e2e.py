from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

from paulsha_hippo import cli
from paulsha_hippo.lib.lifecycle.schema import compute_checksum

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RAW = ("---\nmemory_layer: inbox\nproject: paulshaclaw\nsource_agent: claude\n"
        "source_session: sess-moc\nsource_artifact: research\n"
        'captured_at: "2026-06-03T00:00:00Z"\nprovenance:\n  repo: some-other-repo\n'
        "  commit: c\n  path: nope.md\n---\n# Topic A\nalpha body\n")


@contextmanager
def _home(root: Path):
    home = root / "home"; home.mkdir(parents=True, exist_ok=True)
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        yield


class MocE2ETests(unittest.TestCase):
    def test_dream_moc_makes_obsidian_vault(self):
        with TemporaryDirectory(dir=_REPO_ROOT) as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-06-03" / "s1.md"
            raw.parent.mkdir(parents=True); raw.write_text(_RAW, encoding="utf-8")
            with _home(root):
                rc = cli.main(["dream", "run", "--memory-root", str(root),
                               "--now", "2026-06-03T05:00:00Z", "--promoter", "identity"])
            self.assertEqual(rc, 0)
            # readable filenames + MOC files
            slices = list((root / "knowledge").rglob("*--sl*.md"))
            self.assertTrue(slices)
            self.assertTrue((root / "knowledge" / "wiki-moc.md").exists())
            # no [[..]] in body; checksum intact (gate passes)
            for s in slices:
                text = s.read_text(encoding="utf-8")
                parts = text.split("---\n", 2)
                self.assertEqual(len(parts), 3, f"Expected frontmatter in {s.name}")
                fm = yaml.safe_load(parts[1])
                body = parts[2]
                self.assertNotIn("[[", body)
                # Verify checksum matches (gate passes)
                self.assertEqual(fm.get("checksum"), compute_checksum(body))
            # search finds it
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["search", "alpha", "--memory-root", str(root)])
            self.assertIn("results", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
