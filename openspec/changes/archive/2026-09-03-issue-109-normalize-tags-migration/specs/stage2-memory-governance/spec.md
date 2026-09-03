## ADDED Requirements

### Requirement: 既存非字串 tags 一次性 migration

系統 SHALL 提供一次性 migration CLI `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]`，掃描 knowledge 層 slice frontmatter 的 `tags`，找出含至少一個非字串元素（如裸數字、`null`、嵌套 list）或值為非 list 的既存 slice；掃描 SHALL 無條件過濾 `memory_layer != "knowledge"` 的檔案（含 `<root>/knowledge` 不存在的 fallback 掃描情境），MUST NOT 改寫 inbox/episodic 或一般 markdown 文件。`--dry-run`（預設）SHALL 回報待修 slice 清單與各自以 `normalize_tags()` 正規化後的 tags 預覽，且 MUST NOT 修改任何檔案（bytes 逐位元不變）。`--apply` SHALL 重用 `atomizer/slice_frontmatter.py::normalize_tags()` 做正規化（非 list scalar tags 依 #101 語意整體視為空 list，不另設第二套正規化規則）、`moc/frontmatter_io.py::update()` 落地改寫，語意契約為 **parse-equivalent**：body SHALL 逐位元不變；tags 以外的 frontmatter 欄位 parsed 值 SHALL 不變（`update()` 整份重 dump，YAML 表層形如引號樣式 MAY 正規化），僅允許兩個宣告的型別正規化——原生 datetime 標量正規化為等值 ISO8601 字串（stage3 schema 對 `created_at` 的契約即為字串）、null SHALL 維持 null（不得劣化為字串 `"None"`）；對已無非字串 tags 的 slice SHALL 為 no-op。migration SHALL 為冪等：`--apply` 後重跑 `--dry-run` SHALL 回報 0 個待修 slice，再次 `--apply` SHALL 為 no-op 且 bytes 不變。migration 輸出經後續 `frontmatter_io.update()` 往返（如 retitle）後，tags SHALL 仍全為字串（繼承 #104 型別保真保護）。本 migration MUST NOT 修改 MOC index 的嚴格驗證邏輯，MUST NOT 動 dream/atomize/janitor 既有寫入熱路徑。

#### Scenario: dry-run 精確回報且不動檔案
- **WHEN** memory root 內有 tags 全為字串、tags 含裸 int、tags 含 null 與嵌套 list 的三個 slice，執行 `--dry-run`
- **THEN** 回報恰好 2 個待修 slice 及各自正規化後 tags 預覽，且三個檔案 bytes 逐位元不變

#### Scenario: apply 正規化且冪等（parse-equivalent）
- **WHEN** 對同一 memory root 執行 `--apply`
- **THEN** 待修 slice 的 tags 全為字串、body 逐位元不變、tags 以外欄位 parsed 值不變（僅宣告的 datetime→ISO8601 字串正規化；null 維持 null）；重跑 `--dry-run` 回報 0 個待修 slice；再次 `--apply` 為 no-op 且 bytes 不變

#### Scenario: 打錯 memory-root 不改寫非 knowledge 文件
- **WHEN** `--memory-root` 指向沒有 `knowledge/` 子目錄的目錄且其中有帶非字串 tags 的一般 markdown 或 inbox 層檔案，執行 `--apply`
- **THEN** 僅 `memory_layer: knowledge` 的檔案被掃描與改寫，其餘檔案 bytes 逐位元不變

#### Scenario: 非 list scalar tags 依 #101 語意抹為空 list
- **WHEN** 既存 slice 的 frontmatter 為 `tags: hello`（非 list scalar），先 `--dry-run` 再 `--apply`
- **THEN** dry-run 預覽顯示 `normalized_tags: []`（抹除在 apply 前可見），apply 後 tags 為 `[]`——沿用 `normalize_tags()` 單一真理來源，不為 migration 另設保留 scalar 的第二套規則

#### Scenario: migration 輸出經 update() 往返不劣化
- **WHEN** `--apply` 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle 等下游呼叫）
- **THEN** 重新解析後 tags 仍全為字串，純數字字串 tag 不被剝回 int
