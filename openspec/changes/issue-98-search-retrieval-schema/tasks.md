---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# Issue #98 search FTS5 sanitizer tasks

## 1. CLI 查詢接上 sanitizer

- [x] 1.1 先寫測試：對 `search()` 餵 `"build: f5df394"`、`"tag:123"`、單獨 `"col:"` 等 column-filter 形查詢，斷言不拋 `sqlite3.OperationalError`／`SearchIndexError`，回傳 list（可為空）。
- [x] 1.2 正常關鍵字查詢的零回歸依靠既有測試涵蓋（`tests/test_moc_search.py` 既有無冒號查詢斷言一條未改、全綠）；未另寫修復前後等價測試。另於 1.1 測試內加正向斷言：`"build: f5df394"` sanitize 後 tokens build/f5df394 仍命中 `sl-1`，防 `return []` 式退化實作。
- [x] 1.3 實作：在 `hippo search` 進入 FTS5 前套用 `retrieval.to_fts_query()`（重用，不複製貼上實作）。
- [x] 1.4 全套測試綠；新增 `changelog.d/98-search-fts-sanitizer.md`（type: fix）。
