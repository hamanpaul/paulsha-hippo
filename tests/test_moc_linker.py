from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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
            weights, warnings = linker.materialize_links(root)
            self.assertEqual(warnings, [])
            fm_a, body_a = fio.read(a.read_text(encoding="utf-8"))
            fm_b, _ = fio.read(b.read_text(encoding="utf-8"))
            self.assertIn("[[beta--sl-b]]", fm_a["related"])
            self.assertIn("[[MTK]]", fm_a["related"])
            self.assertIn("[[alpha--sl-a]]", fm_b["related"])  # bidirectional
            self.assertNotIn("[[", body_a)                      # never in body
            self.assertTrue(validate_frontmatter(frontmatter=fm_a, body=body_a).ok)  # checksum intact
            self.assertEqual(fm_a.get("aliases"), ["alpha"])
            self.assertEqual(weights["sl-a"], 2)

    def test_single_bad_slice_fails_soft(self):
        # #16：單一 slice 寫入失敗只記 warning 跳過，其餘照常物化
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "alpha")
            b = _slice(root, "sl-b", "beta")
            relations.append_edge(root, type="relates_to", frm="slice:sl-a", to="slice:sl-b", now="t", config_hash="h")
            real_update = fio.update

            def flaky_update(path, updates):
                if path.name.startswith("alpha--"):
                    raise OSError(28, "No space left on device")
                real_update(path, updates)

            with mock.patch("paulsha_hippo.moc.linker.fio.update", side_effect=flaky_update):
                weights, warnings = linker.materialize_links(root)
            self.assertNotIn("sl-a", weights)
            self.assertIn("sl-b", weights)
            self.assertTrue(any("alpha--sl-a.md" in w for w in warnings))
            fm_b, _ = fio.read(b.read_text(encoding="utf-8"))
            self.assertIn("[[alpha--sl-a]]", fm_b["related"])  # 好 slice 照常完成

    def test_undecodable_file_skipped_with_warning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "alpha")
            kdir = root / "knowledge" / "p"
            (kdir / "broken.md").write_bytes(b"---\nslice_id: sl-bad\n\xff\xfe---\nbody\n")
            weights, warnings = linker.materialize_links(root)
            self.assertIn("sl-a", weights)
            self.assertNotIn("sl-bad", weights)
            self.assertTrue(any("broken.md" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
