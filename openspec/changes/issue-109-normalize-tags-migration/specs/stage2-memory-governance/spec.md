## ADDED Requirements

### Requirement: 既存非字串 tags 一次性 migration

系統 SHALL 提供一次性 migration CLI `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]`，掃描 knowledge 層 slice frontmatter 的 `tags`，找出含至少一個非字串元素（如裸數字、`null`、嵌套 list）的既存 slice。`--dry-run`（預設）SHALL 回報待修 slice 清單與各自以 `normalize_tags()` 正規化後的 tags 預覽，且 MUST NOT 修改任何檔案（bytes 逐位元不變）。`--apply` SHALL 重用 `atomizer/slice_frontmatter.py::normalize_tags()` 做正規化、`moc/frontmatter_io.py::update()` 落地改寫，僅 tags 變動，body 與其他 frontmatter 欄位 SHALL 保持不變；對已無非字串 tags 的 slice SHALL 為 no-op。migration SHALL 為冪等：`--apply` 後重跑 `--dry-run` SHALL 回報 0 個待修 slice，再次 `--apply` SHALL 為 no-op。migration 輸出經後續 `frontmatter_io.update()` 往返（如 retitle）後，tags SHALL 仍全為字串（繼承 #104 型別保真保護）。本 migration MUST NOT 修改 MOC index 的嚴格驗證邏輯，MUST NOT 動 dream/atomize/janitor 既有寫入熱路徑。

#### Scenario: dry-run 精確回報且不動檔案
- **WHEN** memory root 內有 tags 全為字串、tags 含裸 int、tags 含 null 與嵌套 list 的三個 slice，執行 `--dry-run`
- **THEN** 回報恰好 2 個待修 slice 及各自正規化後 tags 預覽，且三個檔案 bytes 逐位元不變

#### Scenario: apply 正規化且冪等
- **WHEN** 對同一 memory root 執行 `--apply`
- **THEN** 待修 slice 的 tags 全為字串、body 與其他 frontmatter 欄位不變；重跑 `--dry-run` 回報 0 個待修 slice；再次 `--apply` 為 no-op

#### Scenario: migration 輸出經 update() 往返不劣化
- **WHEN** `--apply` 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle 等下游呼叫）
- **THEN** 重新解析後 tags 仍全為字串，純數字字串 tag 不被剝回 int
