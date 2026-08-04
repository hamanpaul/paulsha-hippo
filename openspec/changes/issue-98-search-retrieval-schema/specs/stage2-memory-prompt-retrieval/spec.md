# stage2-memory-prompt-retrieval Spec Delta

## MODIFIED Requirements

### Requirement: FTS 查詢淨化純函式

`hippo search` CLI 入口與 `search()` 函式 SHALL 於查詢進入 SQLite FTS5 前套用 `to_fts_query()` 進行淨化，防止 `word:` 形式之 column-filter 形查詢觸發 SQLite `no such column` 或語法錯誤。

#### Scenario: column-filter 形查詢不致語法錯誤

- **WHEN** 對 `search()` 傳入 `build: f5df394`、`tag:123` 或 `col:` 等 column-filter 形查詢
- **THEN** 查詢 MUST NOT 拋出 `sqlite3.OperationalError` 或 `SearchIndexError`，且回傳 list（可為空）
