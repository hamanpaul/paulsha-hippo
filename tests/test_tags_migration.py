from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import tags_migration
from paulsha_hippo.moc import frontmatter_io as fio


def _create_slice(root: Path, rel_path: str, tags: list | None, body: str = "Test body", title: str = "Test Slice") -> Path:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    tags_yaml = ""
    if tags is not None:
        if not tags:
            tags_yaml = "tags: []\n"
        else:
            tags_yaml = "tags:\n" + "\n".join(f"  - {t}" for t in tags) + "\n"
    
    content = (
        "---\n"
        "slice_id: sl-test12345678\n"
        "memory_layer: knowledge\n"
        "project: testprj\n"
        "artifact_kind: report\n"
        f"title: \"{title}\"\n"
        f"{tags_yaml}"
        "---\n"
        f"{body}\n"
    )
    p.write_text(content, encoding="utf-8")
    return p


class NormalizeTagsMigrationTests(unittest.TestCase):
    def test_dry_run_scans_and_reports_non_string_tags_without_modifying_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1. 正常 tags (全為 string)
            p_normal = _create_slice(root, "knowledge/testprj/normal--sl-1.md", ["tag1", "tag2"])
            # 2. 含裸 int tag
            p_int = _create_slice(root, "knowledge/testprj/with_int--sl-2.md", ["tag1", 320, "tag2"])
            # 3. 含 None 與嵌套 list 的 tags
            p_complex = _create_slice(root, "knowledge/testprj/complex--sl-3.md", ["tag1", None, [1, 2]])

            content_normal_before = p_normal.read_bytes()
            content_int_before = p_int.read_bytes()
            content_complex_before = p_complex.read_bytes()

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=False)

            self.assertEqual(warnings, [])
            self.assertEqual(summary["scanned"], 3)
            self.assertEqual(summary["pending"], 2)
            self.assertEqual(summary["updated"], 0)

            # 驗證 2 個待修 slice 與各自的正規化預覽
            pending_map = {item["path"]: item for item in summary["details"]}
            rel_int = str(p_int.relative_to(root))
            rel_complex = str(p_complex.relative_to(root))

            self.assertIn(rel_int, pending_map)
            self.assertIn(rel_complex, pending_map)
            self.assertEqual(pending_map[rel_int]["normalized_tags"], ["tag1", "320", "tag2"])
            self.assertEqual(pending_map[rel_complex]["normalized_tags"], ["tag1"])

            # 確信位元不變 (bytes 比對)
            self.assertEqual(p_normal.read_bytes(), content_normal_before)
            self.assertEqual(p_int.read_bytes(), content_int_before)
            self.assertEqual(p_complex.read_bytes(), content_complex_before)

    def test_apply_normalizes_tags_preserves_other_fields_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p_int = _create_slice(root, "knowledge/testprj/with_int--sl-2.md", ["tag1", 320, "tag2"], body="Body content\nMore lines")
            p_complex = _create_slice(root, "knowledge/testprj/complex--sl-3.md", ["tag1", None, [1, 2]])

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(warnings, [])
            self.assertEqual(summary["updated"], 2)

            # verify tags are strings now
            fm_int, body_int = fio.read(p_int)
            self.assertEqual(fm_int["tags"], ["tag1", "320", "tag2"])
            self.assertTrue(all(isinstance(t, str) for t in fm_int["tags"]))
            self.assertEqual(body_int.strip(), "Body content\nMore lines".strip())

            # rerun dry-run reports 0 pending
            summary2, warnings2 = tags_migration.normalize_tags_migration(root, apply=False)
            self.assertEqual(warnings2, [])
            self.assertEqual(summary2["pending"], 0)

            # rerun apply is no-op
            summary3, warnings3 = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(warnings3, [])
            self.assertEqual(summary3["updated"], 0)

    def test_apply_followed_by_frontmatter_update_maintains_string_tags(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p_int = _create_slice(root, "knowledge/testprj/with_int--sl-2.md", ["tag1", 320, "tag2"])

            tags_migration.normalize_tags_migration(root, apply=True)

            # Simulate retitle / update
            fio.update(p_int, {"title": "Updated Title"})
            fm_updated, _ = fio.read(p_int)
            self.assertEqual(fm_updated["title"], "Updated Title")
            self.assertEqual(fm_updated["tags"], ["tag1", "320", "tag2"])
            self.assertTrue(all(isinstance(t, str) for t in fm_updated["tags"]))


if __name__ == "__main__":
    unittest.main()

