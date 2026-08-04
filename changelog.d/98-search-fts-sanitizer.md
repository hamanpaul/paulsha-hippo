---
type: fix
---
- `hippo search` 進入 FTS5 前改重用 `retrieval.to_fts_query()` 正規化查詢；`build: f5df394`、`tag:123`、`col:` 這類含冒號的輸入不再被 FTS5 誤判成不存在的欄位過濾器並拋 `no such column`。
