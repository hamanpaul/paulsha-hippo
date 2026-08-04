---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# Issue #98 search FTS5 sanitizer tasks

## 1. CLI 查詢接上 sanitizer

- [x] 1.1 先寫測試：對 `search()` 餵 `"build: f5df394"`、`"tag:123"`、單獨 `"col:"` 等 column-filter 形查詢，斷言不拋 `sqlite3.OperationalError`／`SearchIndexError`，回傳 list（可為空）。
- [x] 1.2 先寫測試：正常關鍵字查詢（既有測試語料）結果與修復前一致。
- [ ] 1.3 實作：在 `hippo search` 進入 FTS5 前套用 `retrieval.to_fts_query()`（重用，不複製貼上實作）。
- [ ] 1.4 全套測試綠；新增 `changelog.d/98-search-fts-sanitizer.md`（type: fix）。
