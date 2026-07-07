from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.lib.lifecycle.schema import compute_checksum, validate_frontmatter
from paulsha_hippo.ledger import relations
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.moc import linker


def _slice(root: Path, slice_id: str, title: str) -> Path:
    body = f"body {slice_id}\n"
    path = root / "knowledge" / "p" / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = (f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: p\n"
          f"artifact_kind: research\ntitle: {title}\nchecksum: {compute_checksum(body)}\n"
          f"phase: research\nversion: 1\ncreated_at: 2026-06-03T00:00:00Z\ncreated_by: c\n"
          f"source_session: s\ngate_required: false\ncaptured_at: 2026-06-03T00:00:00Z\n"
          f"source_agent: c\nsupersedes: []\ndistilled_from: c:s\n---\n{body}")
    path.write_text(fm, encoding="utf-8")
    return path


class LinkerTests(unittest.TestCase):
    def test_bidirectional_related_and_entity_links_in_frontmatter_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _slice(root, "sl-a", "alpha")
            b = _slice(root, "sl-b", "beta")
            relations.append_edge(root, type="relates_to", frm="slice:sl-a", to="slice:sl-b", now="t", config_hash="h")
            relations.append_edge(root, type="mentions", frm="slice:sl-a", to="entity:MTK", now="t", config_hash="h")
            weights = linker.materialize_links(root)
            fm_a, body_a = fio.read(a.read_text(encoding="utf-8"))
            fm_b, _ = fio.read(b.read_text(encoding="utf-8"))
            self.assertIn("[[beta--sl-b]]", fm_a["related"])
            self.assertIn("[[MTK]]", fm_a["related"])
            self.assertIn("[[alpha--sl-a]]", fm_b["related"])  # bidirectional
            self.assertNotIn("[[", body_a)                      # never in body
            self.assertTrue(validate_frontmatter(frontmatter=fm_a, body=body_a).ok)  # checksum intact
            self.assertEqual(fm_a.get("aliases"), ["alpha"])
            self.assertEqual(weights["sl-a"], 2)


if __name__ == "__main__":
    unittest.main()
