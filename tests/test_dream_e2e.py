from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli
from paulsha_hippo.ledger import dream

_REPO_ROOT = Path(__file__).resolve().parents[1]

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: sess-dream
source_artifact: research
captured_at: \"2026-06-02T00:00:00Z\"
provenance:
  repo: .
  commit: c
  path: docs/superpowers/plans/2026-06-02-stage2-dream-service.md
---
# Topic A
alpha body
"""


def _tmp_dir() -> TemporaryDirectory[str]:
    return TemporaryDirectory(dir=_REPO_ROOT)


def _seed(root: Path) -> None:
    raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW, encoding="utf-8")


@contextmanager
def _isolated_home(root: Path):
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        yield


class DreamE2ETests(unittest.TestCase):
    def test_dream_run_then_bundle(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _seed(root)

            with _isolated_home(root):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--promoter",
                        "identity",
                    ]
                )
            self.assertEqual(rc, 0)

            last = dream.last_run(root)
            self.assertIsNotNone(last)
            self.assertIn(last["status"], ("ok", "partial"))
            self.assertIn("atomize", last.get("passes", {}))
            self.assertIn("janitor", last.get("passes", {}))

            out = root / "bundle-out"
            with _isolated_home(root):
                rc2 = cli.main(["bundle",
                        "--memory-root",
                        str(root),
                        "--project",
                        "paulshaclaw",
                        "--out",
                        str(out),
                        "--now",
                        "2026-06-02T06:00:00Z",
                    ]
                )
            self.assertEqual(rc2, 0)

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["raw_excluded"])
            self.assertGreaterEqual(manifest["counts"]["slices"], 1)

            # Bundle must contain only distilled slices + ledgers + manifest, never raw inbox sources.
            allowed: set[str] = {"manifest.json", "ledger.jsonl"}
            for path in out.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(out).as_posix()
                if rel in allowed:
                    continue
                self.assertTrue(
                    rel.startswith("slices/") and rel.endswith(".md"),
                    f"unexpected file in bundle: {rel}",
                )

            blob = "".join(
                p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file()
            )
            self.assertNotIn("memory_layer: inbox", blob)

    def test_dream_produced_knowledge_survives_janitor(self):
        # Realistic dream context: the provenance source repo is NOT checked out
        # at the run CWD, so a CWD-relative path probe cannot resolve it. The
        # janitor must NOT spuriously decay freshly atomized knowledge; the
        # produced slices must stay active and be bundleable.
        unresolvable_raw = (
            "---\n"
            "memory_layer: inbox\n"
            "project: paulshaclaw\n"
            "source_agent: claude\n"
            "source_session: sess-unresolvable\n"
            "source_artifact: research\n"
            'captured_at: "2026-06-02T00:00:00Z"\n'
            "provenance:\n"
            "  repo: some-other-repo\n"
            "  commit: c\n"
            "  path: does/not/exist/here.md\n"
            "---\n"
            "# Topic A\n"
            "alpha body\n"
        )
        with _tmp_dir() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(unresolvable_raw, encoding="utf-8")

            with _isolated_home(root):
                rc = cli.main(["dream", "run", "--memory-root", str(root),
                               "--now", "2026-06-02T05:00:00Z", "--promoter", "identity"])
            self.assertEqual(rc, 0)

            lifecycle_path = root / "runtime" / "ledger" / "lifecycle.jsonl"
            if lifecycle_path.exists():
                for line in lifecycle_path.read_text(encoding="utf-8").splitlines():
                    self.assertNotIn('"event_type":"decayed"', line.replace(" ", ""))
                    self.assertNotIn("source_invalid", line)

            out = root / "bundle-out"
            with _isolated_home(root):
                rc2 = cli.main(["bundle", "--memory-root", str(root),
                                "--project", "paulshaclaw", "--out", str(out),
                                "--now", "2026-06-02T06:00:00Z"])
            self.assertEqual(rc2, 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(manifest["counts"]["slices"], 1)


if __name__ == "__main__":
    unittest.main()
