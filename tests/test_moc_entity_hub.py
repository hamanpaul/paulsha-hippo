from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import relations
from paulsha_hippo.moc import entity_hub, runner
from paulsha_hippo.moc import frontmatter_io as fio


def _slice(root: Path, slice_id: str, title: str, project: str = "p") -> Path:
    path = root / "knowledge" / project / f"{title.lower()}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n"
        f"artifact_kind: research\ntitle: {title}\ncaptured_at: 2026-06-03T00:00:00Z\n"
        f"---\nbody {slice_id}\n", encoding="utf-8")
    return path


def _mention(root: Path, slice_id: str, entity: str) -> None:
    relations.append_edge(root, type="mentions", frm=f"slice:{slice_id}",
                          to=f"entity:{entity}", now="t", config_hash="h")


class EntityHubSyncTests(unittest.TestCase):
    def test_stub_created_with_backlinks_and_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "paulsha-cortex")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="2026-08-04T00:00:00Z")
            self.assertEqual(warnings, [])
            self.assertEqual(stats["created"], 1)
            hub = root / "knowledge" / "entities" / "paulsha-cortex.md"
            fm, body = fio.read(hub.read_text(encoding="utf-8"))
            self.assertEqual(fm["memory_layer"], "entity")
            self.assertEqual(fm["entity"], "paulsha-cortex")
            self.assertEqual(fm["entity_kind"], "unclassified")
            self.assertIn("[[alpha--sl-a|Alpha]]", body)
            # 第二輪：內容不變（不重寫 generated_ts、不空轉 churn）
            before = hub.read_text(encoding="utf-8")
            stats2, _ = entity_hub.sync_entity_hubs(root, now="2026-08-05T00:00:00Z")
            self.assertEqual(stats2["created"], 0)
            self.assertEqual(stats2["updated"], 0)
            self.assertEqual(hub.read_text(encoding="utf-8"), before)

    def test_existing_classified_page_preserved_backlinks_refreshed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _slice(root, "sl-b", "Beta")
            _mention(root, "sl-a", "paulsha-cortex")
            _mention(root, "sl-b", "paulsha-cortex")
            hub = root / "knowledge" / "entities" / "paulsha-cortex.md"
            hub.parent.mkdir(parents=True, exist_ok=True)
            hub.write_text(
                '---\nmemory_layer: entity\nentity: "paulsha-cortex"\n'
                'entity_kind: project\ncanonical_moc: "some-moc"\n'
                'generated_ts: 2026-08-04T00:00:00Z\ngenerated_by: moc-entity-repair\n---\n'
                "# paulsha-cortex\n\n工作流調度核心專案\n\n"
                "## 所屬 MOC\n- [[some-moc|proj]]\n\n"
                "## 反向連結（1 篇筆記提及）\n- [[stale--sl-x|Stale]] — p\n",
                encoding="utf-8")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="2026-08-05T00:00:00Z")
            self.assertEqual(warnings, [])
            self.assertEqual(stats["updated"], 1)
            text = hub.read_text(encoding="utf-8")
            fm, body = fio.read(text)
            self.assertEqual(fm["entity_kind"], "project")          # 分類保留
            self.assertEqual(fm["canonical_moc"], "some-moc")       # MOC 指向保留
            self.assertIn("工作流調度核心專案", body)                 # 描述保留
            self.assertIn("## 所屬 MOC", body)                       # 其他段落保留
            self.assertIn("[[alpha--sl-a|Alpha]]", body)             # 反向連結重刷
            self.assertIn("[[beta--sl-b|Beta]]", body)
            self.assertNotIn("stale--sl-x", body)                    # 舊清單被替換
            self.assertIn("反向連結（2 篇筆記提及）", body)

    def test_alias_mentions_folded_into_canonical(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "Hippo")
            ent_dir = root / "knowledge" / "entities"
            ent_dir.mkdir(parents=True, exist_ok=True)
            (ent_dir / "hippo.md").write_text(
                '---\nmemory_layer: entity\nentity: "hippo"\nentity_kind: concept\n'
                'generated_ts: t\ngenerated_by: moc-entity-repair\n---\n# hippo\n',
                encoding="utf-8")
            alias_text = ('---\nmemory_layer: entity\nentity: "Hippo"\n'
                          'entity_kind: alias\nalias_of: "hippo"\n'
                          'generated_ts: t\ngenerated_by: moc-entity-repair\n---\n'
                          "# Hippo\n\n→ 本條目為 [[hippo]] 的別名。\n")
            (ent_dir / "Hippo.md").write_text(alias_text, encoding="utf-8")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t2")
            self.assertEqual(warnings, [])
            canonical = (ent_dir / "hippo.md").read_text(encoding="utf-8")
            self.assertIn("[[alpha--sl-a|Alpha]]", canonical)
            self.assertIn("（經 Hippo）", canonical)                 # 變體歸戶標註
            self.assertEqual((ent_dir / "Hippo.md").read_text(encoding="utf-8"),
                             alias_text)                             # 別名頁不動

    def test_anchor_entity_creates_prefix_section_and_literal_page(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "PR #54")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            self.assertEqual(warnings, [])
            ent_dir = root / "knowledge" / "entities"
            self.assertTrue((ent_dir / "PR #54.md").exists())        # 字面頁
            prefix = (ent_dir / "PR.md").read_text(encoding="utf-8")
            self.assertIn("## 54", prefix)                           # 錨點段落
            self.assertIn("[[alpha--sl-a|Alpha]]", prefix)
            self.assertEqual(stats["anchor_sections"], 1)
            # 再跑一輪：段落已存在，不重複追加
            stats2, _ = entity_hub.sync_entity_hubs(root, now="t2")
            self.assertEqual(stats2["anchor_sections"], 0)
            self.assertEqual(prefix, (ent_dir / "PR.md").read_text(encoding="utf-8"))

    def test_entity_matching_slice_stem_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", path.stem)   # entity 名 == 筆記檔 stem
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            self.assertEqual(warnings, [])
            self.assertEqual(stats["skipped_slice_collision"], 1)
            self.assertFalse((root / "knowledge" / "entities").exists())

    def test_dry_run_writes_nothing_and_reports_actions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "paulsha-cortex")
            _mention(root, "sl-a", "PR #54")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t", apply=False)
            self.assertEqual(warnings, [])
            self.assertFalse((root / "knowledge" / "entities").exists())
            self.assertEqual(stats["created"], 3)   # cortex + PR #54 字面頁 + PR 前綴頁
            self.assertEqual(stats["anchor_sections"], 1)
            actions = {(a["action"], a["entity"]) for a in stats["actions"]}
            self.assertIn(("created", "paulsha-cortex"), actions)

    def test_unreadable_hub_page_fails_soft(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "paulsha-cortex")
            ent_dir = root / "knowledge" / "entities"
            ent_dir.mkdir(parents=True, exist_ok=True)
            (ent_dir / "broken.md").write_bytes(b"---\nmemory_layer: entity\n\xff\xfe---\n")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            self.assertTrue(any("broken.md" in w for w in warnings))
            self.assertEqual(stats["created"], 1)   # 其餘照常

    def test_path_traversal_components_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "../escape")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            hub_root = root / "knowledge" / "entities"
            self.assertTrue((hub_root / "escape.md").exists())       # `..` 成分被剝除
            self.assertFalse((root / "knowledge" / "escape.md").exists())

    def test_anchor_section_refreshed_on_new_mentions_and_rename(self):
        # review finding：錨點段落不可 append-once 凍結——新 mentions 要進場、
        # slice 改名（naming.reconcile 常態）後不可留死連結
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "PR #54")
            entity_hub.sync_entity_hubs(root, now="t")
            _slice(root, "sl-b", "Beta")
            _mention(root, "sl-b", "PR #54")
            stats, _ = entity_hub.sync_entity_hubs(root, now="t2")
            prefix = (root / "knowledge" / "entities" / "PR.md")
            text = prefix.read_text(encoding="utf-8")
            self.assertIn("[[beta--sl-b|Beta]]", text)              # 新 mention 進段落
            self.assertEqual(stats["anchor_sections"], 1)
            # slice 改名（模擬 reconcile rename）→ 段落刷新、不留死 stem
            a.rename(a.with_name("renamed--sl-a.md"))
            entity_hub.sync_entity_hubs(root, now="t3")
            text = prefix.read_text(encoding="utf-8")
            self.assertIn("[[renamed--sl-a|Alpha]]", text)
            self.assertNotIn("alpha--sl-a", text)

    def test_empty_backlinks_clears_stale_section(self):
        # review finding：提及 slice 全數消失（prune/dedupe）後不可殘留死連結
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "widget")
            entity_hub.sync_entity_hubs(root, now="t")
            a.unlink()
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t2")
            self.assertEqual(warnings, [])
            self.assertEqual(stats["updated"], 1)
            text = (root / "knowledge" / "entities" / "widget.md").read_text(encoding="utf-8")
            self.assertIn("反向連結（0 篇筆記提及）", text)
            self.assertNotIn("alpha--sl-a", text)
            # 再跑一輪：冪等
            stats2, _ = entity_hub.sync_entity_hubs(root, now="t3")
            self.assertEqual(stats2["updated"], 0)

    def test_foreign_page_untouched_reported_structural_not_warning(self):
        # review finding：兩個 pass 都不可寫外來檔；且結構性狀態不可進 warnings
        # （#101 教訓：每輪必再現的 warning 會讓 dream 永久 partial）
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "PR")
            _mention(root, "sl-a", "PR #54")
            ent_dir = root / "knowledge" / "entities"
            ent_dir.mkdir(parents=True, exist_ok=True)
            foreign = "使用者手放的普通筆記，無 frontmatter。\n"
            (ent_dir / "PR.md").write_text(foreign, encoding="utf-8")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            self.assertEqual(warnings, [])                           # 不污染 dream clean
            self.assertEqual((ent_dir / "PR.md").read_text(encoding="utf-8"), foreign)
            reasons = {s["entity"] for s in stats["structural"]}
            self.assertIn("PR", reasons)                             # pass 1 跳過
            self.assertIn("PR #54", reasons)                         # pass 2 跳過
            self.assertEqual(stats["skipped_structural"], 2)

    def test_newline_entity_name_round_trips_idempotently(self):
        # review finding：_yaml_quote 需跳脫 \n（對齊 frontmatter_io._scalar #139），
        # 否則第二輪起認不得自己建的頁、每輪誤報
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "Alpha")
            _mention(root, "sl-a", "Foo\nBar")
            stats, warnings = entity_hub.sync_entity_hubs(root, now="t")
            self.assertEqual(warnings, [])
            self.assertEqual(stats["created"], 1)
            stats2, warnings2 = entity_hub.sync_entity_hubs(root, now="t2")
            self.assertEqual(warnings2, [])
            self.assertEqual(stats2["created"], 0)
            self.assertEqual(stats2["structural"], [])               # 認得自己建的頁

    def test_cli_exit_codes_for_apply_failures_and_dry_run_pending(self):
        # review finding：apply 失敗要以 exit code 回報（比照 _rekey/_prune_listed）
        from unittest import mock

        from paulsha_hippo import cli as hippo_cli
        from paulsha_hippo.moc import entity_hub as hub_mod

        with TemporaryDirectory() as tmp:
            base = ["knowledge", "entity-hubs", "--memory-root", tmp]
            ok = ({"actions": [], "structural": []}, [])
            pending = ({"actions": [{"action": "created", "entity": "e", "path": "p"}],
                        "structural": []}, [])
            failed = ({"actions": [], "structural": []}, ["entity-hub: 'e' 同步失敗（IO）"])
            with mock.patch.object(hub_mod, "sync_entity_hubs", return_value=ok):
                self.assertEqual(hippo_cli.main(base + ["--apply"]), 0)
                self.assertEqual(hippo_cli.main(base + ["--dry-run"]), 0)
            with mock.patch.object(hub_mod, "sync_entity_hubs", return_value=pending):
                self.assertEqual(hippo_cli.main(base + ["--dry-run"]), 1)   # 待辦→健檢紅
                self.assertEqual(hippo_cli.main(base + ["--apply"]), 0)     # 已套用→綠
            with mock.patch.object(hub_mod, "sync_entity_hubs", return_value=failed):
                self.assertEqual(hippo_cli.main(base + ["--apply"]), 1)     # 失敗→紅
                self.assertEqual(hippo_cli.main(base + ["--dry-run"]), 1)

    def test_run_moc_integration_creates_hubs_and_reports_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "Alpha")
            _mention(root, "sl-1", "paulsha-cortex")
            result = runner.run_moc(root, now="2026-08-04T00:00:00Z")
            self.assertEqual(result["entity_hubs"]["created"], 1)
            self.assertNotIn("actions", result["entity_hubs"])       # 摘要不含動作清單
            self.assertTrue(
                (root / "knowledge" / "entities" / "paulsha-cortex.md").exists())
            # entity 頁不影響 index coverage 的 eligible/indexed 對齊
            cov = result["index_coverage"]
            self.assertEqual(cov["eligible"], cov["indexed"])


if __name__ == "__main__":
    unittest.main()
