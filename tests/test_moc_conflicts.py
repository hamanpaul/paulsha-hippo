from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import record_source
from paulsha_hippo.replay import selector


def _moc(root: Path) -> None:
    p = root / "knowledge" / "wiki-moc.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n- [[a--sl-1]]\n", encoding="utf-8")


def _slice(root: Path) -> None:
    p = root / "knowledge" / "p" / "a--sl-1.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: p\n"
                 "source_agent: c\nsource_session: s\ncaptured_at: 2026-06-03T00:00:00Z\n"
                 "provenance:\n  repo: r\n  commit: c\n  path: x\n---\nbody\n", encoding="utf-8")


class C4ExclusionTests(unittest.TestCase):
    def test_record_source_skips_moc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _moc(root); _slice(root)
            records, _ = record_source.iter_records(root / "knowledge")
            self.assertEqual([r.record_id for r in records], ["sl-1"])  # moc not a record

    def test_selector_skips_moc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _moc(root); _slice(root)
            paths = selector.select(root, project="p")
            self.assertTrue(all(p.name != "wiki-moc.md" for p in paths))


if __name__ == "__main__":
    unittest.main()
