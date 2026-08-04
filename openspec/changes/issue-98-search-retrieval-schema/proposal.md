---
status: accepted
work_item: issue-98-search-retrieval-schema
---

## Why

`hippo search` CLI 對含 `word:` 形式的查詢（如 `build: f5df394`）會拋 `no such column: build`。SQLite FTS5 對 `MATCH ?` 綁定參數仍會解析查詢語法；查詢字串含 `word:` 時 `word` 被當 column-filter，欄位不存在即報 `no such column`。

## What Changes

- 在 `hippo search` CLI 與 `search()` 進入 FTS5 前套用 `retrieval.to_fts_query()` sanitizer。
- 新增單元測試驗證 `build: f5df394`、`tag:123`、`col:` 等 column-filter 形查詢不拋 `SearchIndexError`。

## Non-Goals

- 不新增任何查詢語法設計。
