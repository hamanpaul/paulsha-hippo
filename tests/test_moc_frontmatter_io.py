from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.lib.lifecycle.schema import compute_checksum, validate_frontmatter
from paulsha_hippo.moc import frontmatter_io as fio

_SLICE = (
    "---\n"
    "phase: research\nproject: paulshaclaw\nslice_id: sl-abc\nartifact_kind: research\n"
    "version: 1\ncreated_at: 2026-06-03T00:00:00Z\ncreated_by: claude\n"
    "source_session: s1\ngate_required: false\nchecksum: __CK__\n"
    "memory_layer: knowledge\nsource_agent: claude\ncaptured_at: 2026-06-03T00:00:00Z\n"
    "supersedes: []\ndistilled_from: claude:s1\n"
    "provenance:\n  repo: r\n  commit: c\n  path: p\n"
    "---\n"
    "BODY LINE ONE\nBODY LINE TWO\n"
)


def _slice_text() -> str:
    body = "BODY LINE ONE\nBODY LINE TWO\n"
    return _SLICE.replace("__CK__", compute_checksum(body))


class FrontmatterIoTests(unittest.TestCase):
    def test_read_splits_frontmatter_and_body(self):
        fm, body = fio.read(_slice_text())
        self.assertEqual(fm["slice_id"], "sl-abc")
        self.assertEqual(body, "BODY LINE ONE\nBODY LINE TWO\n")

    def test_rewrite_preserves_body_and_checksum(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.md"
            path.write_text(_slice_text(), encoding="utf-8")
            fio.update(path, {"title": "Alpha", "aliases": ["Alpha"],
                              "related": ["[[Beta--sl-2]]", "[[MTK]]"]})
            fm, body = fio.read(path.read_text(encoding="utf-8"))
            self.assertEqual(fm["title"], "Alpha")
            self.assertEqual(fm["related"], ["[[Beta--sl-2]]", "[[MTK]]"])
            # body and checksum unchanged -> Stage 3 still validates
            self.assertEqual(body, "BODY LINE ONE\nBODY LINE TWO\n")
            result = validate_frontmatter(frontmatter=fm, body=body)
            self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
