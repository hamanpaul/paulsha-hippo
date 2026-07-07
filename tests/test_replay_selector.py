from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.replay import selector
from paulsha_hippo.ledger import relations, lifecycle


class ReplaySelectorTests(unittest.TestCase):
    def _write_slice(self, root: Path, slice_id: str, project: str = "p", tags: list | None = None):
        # place slices under knowledge/<project>/<slice_id>.md to match real layout
        k = root / "knowledge" / project
        k.mkdir(parents=True, exist_ok=True)
        p = k / f"{slice_id}.md"
        fm_lines = [
            "---",
            "memory_layer: knowledge",
            f"slice_id: {slice_id}",
            f"project: {project}",
        ]
        if tags is not None:
            # write tags as a valid YAML block list
            fm_lines.append("tags:")
            for t in tags:
                fm_lines.append(f"  - {t}")
        fm_lines.append("---")
        fm_lines.append("body")
        p.write_text("\n".join(fm_lines), encoding="utf-8")
        return p

    def test_project_filter(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            self._write_slice(root, "sl-a", project="proj1")
            self._write_slice(root, "sl-b", project="proj2")
            got = selector.select(root, project="proj1")
            self.assertEqual([p.name for p in got], ["sl-a.md"])

    def test_tags_any_match(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            self._write_slice(root, "sl-a", project="p", tags=["t1", "t2"]) 
            self._write_slice(root, "sl-b", project="p", tags=["t3"]) 
            got = selector.select(root, tags=["t2"]) 
            self.assertEqual([p.name for p in got], ["sl-a.md"])

    def test_entity_facet(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p = self._write_slice(root, "sl-ent", project="p")
            # append relations edge: mentions from slice:sl-ent -> entity:FOO
            relations.append_edge(root, type="mentions", frm="slice:sl-ent", to="entity:FOO", now="x", config_hash="h")
            got = selector.select(root, entity="FOO")
            self.assertEqual([x.name for x in got], [p.name])

    def test_active_set_excludes_decayed_by_default(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p1 = self._write_slice(root, "sl-1", project="p")
            p2 = self._write_slice(root, "sl-2", project="p")
            # mark sl-2 as decayed
            lifecycle.append_event(root / "runtime" / "ledger" / "lifecycle.jsonl", record_id="sl-2", event_type="decayed", source="t", reason="r", actor="a", ts="2026-06-01T00:00:00Z")
            got = selector.select(root, project="p")
            self.assertEqual([x.name for x in got], [p1.name])
            # include decayed
            got2 = selector.select(root, project="p", include_decayed=True)
            self.assertCountEqual([x.name for x in got2], [p1.name, p2.name])

    def test_no_facet_raises(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(selector.SelectorError):
                selector.select(root)

    def test_symlinked_files_ignored(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            real = self._write_slice(root, "sl-real", project="p")
            symlink = root / "knowledge" / "p" / "sl-link.md"
            # create a symlink pointing to the real file
            symlink.symlink_to(real)
            got = selector.select(root, project="p")
            # should only return the real file path, not the symlink
            self.assertEqual([x.name for x in got], [real.name])

    def test_frontmatter_requires_fence_lines(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            k = root / "knowledge" / "p"
            k.mkdir(parents=True, exist_ok=True)
            p = k / "sl-fence.md"
            content = "\n".join([
                "---",
                "memory_layer: knowledge",
                "slice_id: sl-fence",
                "project: p",
                "note: 'this --- is not a fence'",
                "---",
                "body",
            ])
            p.write_text(content, encoding="utf-8")
            got = selector.select(root, project="p")
            self.assertEqual([x.name for x in got], ["sl-fence.md"])

    def test_duplicate_slice_id_rejected(self):
        """Regression: duplicates of slice_id should be rejected consistently."""
        with TemporaryDirectory() as td:
            root = Path(td)
            # create two distinct files that declare the same slice_id
            self._write_slice(root, "dup", project="projA")
            self._write_slice(root, "dup", project="projB")
            # discovery should detect the duplicate slice_id and raise
            with self.assertRaises(selector.SelectorError):
                selector.select(root, project="projA")

    def test_project_and_tags_and_composition(self):
        """Facets compose with AND semantics (project + tags)."""
        with TemporaryDirectory() as td:
            root = Path(td)
            self._write_slice(root, "sl-a", project="proj1", tags=["x", "y"]) 
            self._write_slice(root, "sl-b", project="proj1", tags=["y"]) 
            self._write_slice(root, "sl-c", project="proj2", tags=["x"]) 
            got = selector.select(root, project="proj1", tags=["x"]) 
            self.assertEqual([p.name for p in got], ["sl-a.md"])


if __name__ == "__main__":
    unittest.main()
