from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.moc import runner


def _slice(root: Path, slice_id: str, title: str) -> None:
    path = root / "knowledge" / "p" / f"{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: p\n"
                    f"artifact_kind: research\ntitle: {title}\ncaptured_at: 2026-06-03T00:00:00Z\n"
                    f"---\nbody {slice_id}\n", encoding="utf-8")


class RunnerTests(unittest.TestCase):
    def test_run_moc_renames_links_mocs_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "Alpha")
            result = runner.run_moc(root, now="2026-06-03T00:00:00Z")
            self.assertTrue((root / "knowledge" / "p" / "alpha--sl-1.md").exists())
            self.assertTrue((root / "knowledge" / "wiki-moc.md").exists())
            self.assertTrue((root / "runtime" / "indexes" / "retrieval.db").exists())
            self.assertIn("indexed", result)

    def test_idempotent_rerun(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "Alpha")
            runner.run_moc(root, now="2026-06-03T00:00:00Z")
            wiki1 = (root / "knowledge" / "wiki-moc.md").read_text(encoding="utf-8")
            runner.run_moc(root, now="2026-06-03T00:00:00Z")
            wiki2 = (root / "knowledge" / "wiki-moc.md").read_text(encoding="utf-8")
            self.assertEqual(wiki1, wiki2)

    def test_run_moc_reports_index_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "Alpha")
            result = runner.run_moc(root, now="2026-06-03T00:00:00Z")
            cov = result["index_coverage"]
            self.assertEqual(cov["eligible"], 1)
            self.assertEqual(cov["indexed"], 1)
            self.assertGreaterEqual(cov["scanned"], 2)  # slice + build_mocs 產生的 moc 檔
            self.assertGreaterEqual(sum(cov["pool_excluded"].values()), 1)


if __name__ == "__main__":
    unittest.main()
