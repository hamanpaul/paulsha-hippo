from __future__ import annotations

import unittest
from datetime import datetime
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
            tags_yaml = "tags:\n" + "\n".join(f"  - {'null' if t is None else t}" for t in tags) + "\n"

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


# Production-shaped fixture（issue #109 review MAJOR）：仿 atomizer render() 的
# canonical 形狀，涵蓋 update() 整份重 dump 時所有已知的表層/型別變化熱點——
# json 引號 free-text 欄位、未引號 YAML datetime、裸 null distiller 欄位、
# 非空 provenance/distiller dict、引號數字樣字串（version: "1"）。
_PRODUCTION_SHAPED_SLICE = (
    "---\n"
    "phase: review\n"
    'project: "github.com/hamanpaul/intellidbgkit"\n'
    "slice_id: sl-1010b51101ebe096\n"
    "artifact_kind: report\n"
    'version: "1"\n'
    "created_at: 2026-08-02 07:52:28\n"
    "created_by: codex\n"
    'source_session: "codex-session-0102"\n'
    "gate_required: false\n"
    "checksum: abc123checksum\n"
    "memory_layer: knowledge\n"
    "source_agent: codex\n"
    'captured_at: "2026-08-02T07:52:28Z"\n'
    "supersedes: []\n"
    'distilled_from: "codex:codex-session-0102"\n'
    'title: "Terminal directory seal: crash recovery"\n'
    "tags:\n"
    "  - terminal\n"
    "  - 320\n"
    "  - crash-recovery\n"
    "provenance:\n"
    '  repo: "hamanpaul/intellidbgkit"\n'
    '  commit: "abc123"\n'
    '  path: "docs/seal.md"\n'
    "distiller:\n"
    '  profile_id: "codex"\n'
    "  tier: 1\n"
    "  fallback_reason: null\n"
    "---\n"
    "Body line one\n"
    "Body line two\n"
)


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

            fm_int_before, body_int_before = fio.read(p_int.read_text(encoding="utf-8"))

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(warnings, [])
            self.assertEqual(summary["updated"], 2)

            # verify tags are strings now, everything else parse-equivalent
            fm_int, body_int = fio.read(p_int.read_text(encoding="utf-8"))
            self.assertEqual(fm_int["tags"], ["tag1", "320", "tag2"])
            self.assertTrue(all(isinstance(t, str) for t in fm_int["tags"]))
            self.assertEqual(body_int, body_int_before)
            expected = dict(fm_int_before)
            expected["tags"] = ["tag1", "320", "tag2"]
            self.assertEqual(fm_int, expected)

            # rerun dry-run reports 0 pending
            summary2, warnings2 = tags_migration.normalize_tags_migration(root, apply=False)
            self.assertEqual(warnings2, [])
            self.assertEqual(summary2["pending"], 0)

            # rerun apply is no-op
            summary3, warnings3 = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(warnings3, [])
            self.assertEqual(summary3["updated"], 0)

    def test_apply_on_production_shaped_slice_is_parse_equivalent_outside_tags(self):
        """MAJOR 收斂鎖（issue #109 review）：apply 走 ``frontmatter_io.update()``
        整份重 dump，YAML 表層形（引號樣式）可正規化，因此 SHALL 鎖的是
        parse-equivalent 而非逐位元——body 逐位元不變；tags 以外所有 frontmatter
        欄位 parsed 值不變，僅一個已宣告的型別正規化例外：

        - 未引號 YAML datetime 標量（如 ``created_at: 2026-08-02 07:52:28``）
          正規化為等值 ISO8601 字串。stage3 schema 對 ``created_at`` 的契約本來
          就是字串（``lib/lifecycle/schema.py::_is_iso8601`` 對 datetime 物件
          直接 FAIL），此轉換是朝 schema 收斂的正規化而非劣化。
        - 裸 null（``fallback_reason: null``）維持 null——鎖住 #109 review 抓到
          的 ``_scalar(None)`` → ``None`` 字面值 → 重讀劣化成字串 "None"。
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "knowledge" / "prj" / "terminal-directory-seal--sl-1010b51101ebe096.md"
            p.parent.mkdir(parents=True)
            p.write_text(_PRODUCTION_SHAPED_SLICE, encoding="utf-8")

            fm_before, body_before = fio.read(_PRODUCTION_SHAPED_SLICE)
            # fixture 前提自檢：datetime 未引號、null 裸寫、version 為引號字串
            self.assertIsInstance(fm_before["created_at"], datetime)
            self.assertIsNone(fm_before["distiller"]["fallback_reason"])
            self.assertIsInstance(fm_before["version"], str)

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(warnings, [])
            self.assertEqual(summary["updated"], 1)

            text_after = p.read_text(encoding="utf-8")
            fm_after, body_after = fio.read(text_after)

            # body 逐位元不變
            self.assertEqual(body_after, body_before)

            # tags 以外全欄位 parsed-equality（含巢狀 dict 與型別）
            expected = dict(fm_before)
            expected["tags"] = ["terminal", "320", "crash-recovery"]
            expected["created_at"] = str(fm_before["created_at"])  # 宣告的 datetime→ISO8601 str 正規化
            self.assertEqual(fm_after, expected)

            # 劣化熱點逐一點名：
            self.assertTrue(all(isinstance(t, str) for t in fm_after["tags"]))
            self.assertIsInstance(fm_after["created_at"], str)
            self.assertEqual(fm_after["created_at"], "2026-08-02 07:52:28")
            self.assertIsNone(fm_after["distiller"]["fallback_reason"], "null 劣化成字串 'None'")
            self.assertIn("fallback_reason: null", text_after)
            self.assertIsInstance(fm_after["version"], str, "引號數字樣字串被剝回 int（#102/#104 回歸）")
            self.assertEqual(fm_after["title"], "Terminal directory seal: crash recovery")
            self.assertEqual(fm_after["provenance"], {"repo": "hamanpaul/intellidbgkit", "commit": "abc123", "path": "docs/seal.md"})
            self.assertEqual(fm_after["distiller"]["tier"], 1)

            # 冪等：dry-run 歸零、再 apply 為 no-op 且 bytes 穩定
            bytes_after_first_apply = p.read_bytes()
            summary2, _ = tags_migration.normalize_tags_migration(root, apply=False)
            self.assertEqual(summary2["pending"], 0)
            summary3, _ = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(summary3["updated"], 0)
            self.assertEqual(p.read_bytes(), bytes_after_first_apply)

    def test_missing_knowledge_dir_never_touches_non_knowledge_markdown(self):
        """MINOR 收斂（issue #109 review）：--memory-root 打錯（指到沒有
        knowledge/ 的目錄）時，memory_layer 過濾必須無條件生效（repo 慣例，
        比照 rekey.py/linker.py），fallback 掃描不得改寫 inbox/episodic 或一般
        markdown 文件。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)  # 沒有 knowledge/ 子目錄
            plain = root / "notes" / "plain-doc.md"
            plain.parent.mkdir(parents=True)
            plain.write_text("---\ntitle: doc\ntags:\n  - 320\n---\nplain body\n", encoding="utf-8")
            inbox = root / "inbox" / "item.md"
            inbox.parent.mkdir(parents=True)
            inbox.write_text("---\nmemory_layer: inbox\ntags:\n  - 320\n---\ninbox body\n", encoding="utf-8")
            # root 直下的 knowledge 層 slice（fallback 掃描的唯一合法對象）
            direct = root / "direct--sl-9.md"
            direct.write_text("---\nslice_id: sl-9\nmemory_layer: knowledge\ntags:\n  - 320\n---\ndirect body\n", encoding="utf-8")

            plain_before = plain.read_bytes()
            inbox_before = inbox.read_bytes()

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=True)

            self.assertEqual(warnings, [])
            self.assertEqual(summary["scanned"], 1)
            self.assertEqual(summary["updated"], 1)
            self.assertEqual(plain.read_bytes(), plain_before)
            self.assertEqual(inbox.read_bytes(), inbox_before)
            fm, _ = fio.read(direct.read_text(encoding="utf-8"))
            self.assertEqual(fm["tags"], ["320"])

    def test_scalar_tags_normalize_to_empty_list_locked_decision(self):
        """決策鎖（issue #109 review）：非 list scalar tags（如 ``tags: hello``）
        沿用 #101 單一真理來源 ``normalize_tags()`` 的語意——非 list 整體視為
        空 list，migration 不另設第二套正規化規則（設計 D2 的取捨）。dry-run
        預覽會顯示 ``normalized_tags: []``，操作者在 apply 前可見這個抹除。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "knowledge" / "testprj" / "scalar--sl-4.md"
            p.parent.mkdir(parents=True)
            p.write_text(
                "---\nslice_id: sl-4\nmemory_layer: knowledge\ntags: hello\n---\nbody\n",
                encoding="utf-8",
            )

            summary, warnings = tags_migration.normalize_tags_migration(root, apply=False)
            self.assertEqual(warnings, [])
            self.assertEqual(summary["pending"], 1)
            self.assertEqual(summary["details"][0]["normalized_tags"], [])

            summary2, _ = tags_migration.normalize_tags_migration(root, apply=True)
            self.assertEqual(summary2["updated"], 1)
            fm, _ = fio.read(p.read_text(encoding="utf-8"))
            self.assertEqual(fm["tags"], [])

            summary3, _ = tags_migration.normalize_tags_migration(root, apply=False)
            self.assertEqual(summary3["pending"], 0)

    def test_apply_followed_by_frontmatter_update_maintains_string_tags(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p_int = _create_slice(root, "knowledge/testprj/with_int--sl-2.md", ["tag1", 320, "tag2"])

            tags_migration.normalize_tags_migration(root, apply=True)

            # Simulate retitle / update
            fio.update(p_int, {"title": "Updated Title"})
            fm_updated, _ = fio.read(p_int.read_text(encoding="utf-8"))
            self.assertEqual(fm_updated["title"], "Updated Title")
            self.assertEqual(fm_updated["tags"], ["tag1", "320", "tag2"])
            self.assertTrue(all(isinstance(t, str) for t in fm_updated["tags"]))


if __name__ == "__main__":
    unittest.main()
