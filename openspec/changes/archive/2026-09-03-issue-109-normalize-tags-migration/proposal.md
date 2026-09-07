---
status: accepted
work_item: issue-109-normalize-tags-migration
---

# Issue #109 既存非字串 tags 一次性 migration 提案

## Why

2026-08-04 部署 candidate `1299fa1`（含 #103 tag 正規化修復）後，第一輪 dream cycle
仍判定 `partial`：既存磁碟檔案
（`knowledge/github.com-hamanpaul-intellidbgkit--p-7ae7dbfe5516/terminal-directory-seal-and-crash-recovery--sl-1010b51101ebe096.md`，
codex 於 2026-08-02T07:52 蒸餾產生，早於修復部署）frontmatter `tags` 含裸數字 `320`，
MOC index 每輪重建判 `invalid tags type` 而排除、記 warning，令 orchestrator
`moc_clean=False`，整輪永久 `partial`，與 #101 症狀同構（sticky diagnostic 反模式）。

#103 修復方向正確但範圍侷限於「未來新寫入路徑」（`build_from_proposal()` 呼叫
`normalize_tags()`），已落地的舊檔案不會被回溯修復。issue #109 原文第 2 段描述的
`_scalar()` 純數字字串引號劣化風險已由 PR #104（closes #102）修復並含 round-trip
測試（見 issue #109 comment「範圍更新（batch 前置分析）」），本提案不重工，只涵蓋
剩餘範圍：既存壞資料的一次性 migration。

## What Changes

- 新增一次性 janitor/migration CLI
  `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]`，仿 repo
  既有 `retitle.py`／`rekey.py`／`moc/entity_hub.py` 的 dry-run/apply 模板。
- 新模組 `paulsha_hippo/tags_migration.py`：掃描 knowledge 層 markdown，找出 frontmatter
  `tags` 含非字串元素的既存 slice；`--dry-run` 僅列出待修清單與正規化後 tags 預覽；
  `--apply` 重用 `atomizer/slice_frontmatter.py::normalize_tags()` 正規化、
  `moc/frontmatter_io.py::update()` 落地改寫。
- `paulsha_hippo/cli.py` 接線新子命令。
- `moc/frontmatter_io.py::_scalar()` 型別保真修正（review 收斂）：None 改輸出
  YAML `null`（原輸出 `None` 字面值，PyYAML 不視為 null，`update()` 往返後劣化
  成字串 `"None"`），比照 #102/#104 精神；附直接 round-trip 測試。
- 新增回歸測試：dry-run 精確計數與 bytes 不變、apply 後 tags 全為字串且
  parse-equivalent（body 逐位元不變、其他欄位 parsed 值不變，僅宣告的
  datetime→ISO8601 字串正規化與 null 保真；production-shaped fixture 全欄位
  斷言）、無條件 memory_layer 過濾（打錯 --memory-root 不改寫非 knowledge
  文件）、scalar tags→[] 決策鎖、冪等（apply 後 dry-run 為 0、再次 apply 為
  no-op 且 bytes 不變）、apply 後呼叫 `frontmatter_io.update()` 坐實 #104
  round-trip 保護。
- 新增 `changelog.d/109-normalize-tags-migration.md`（type: fix）。

## Impact

- 影響範圍：新增獨立 CLI 子命令與新模組；不修改 MOC index 的嚴格驗證邏輯（維持
  fail-soft 最後防線），不動 dream/atomize/janitor 既有熱路徑的呼叫結構。唯一
  申報的共用路徑修正：`frontmatter_io._scalar()` None→`null` 一行型別保真修正
  ——只影響「原本就會劣化成字串 `"None"`」的 None 序列化，附直接測試。
- 風險：低——重用單一真理來源的正規化（`normalize_tags()`）與已修復的序列化
  （`frontmatter_io.update()`，PR #104），無新序列化邏輯，migration 為純附加、
  可重跑、有 dry-run。
- Authority：`docs/superpowers/plans/2026-08-04-issue-109-normalize-tags-migration.md`、
  spec/design 同名對。
