---
type: fix
---
- `hippo search` 進入 FTS5 前改重用 `retrieval.to_fts_query()` 正規化查詢；`build: f5df394`、`tag:123`、`col:` 這類含冒號的輸入不再被 FTS5 誤判成不存在的欄位過濾器並拋 `no such column`。
- 語意變更揭露（非零成本 bugfix）：多詞查詢的比對語意由 FTS5 隱式 AND 收斂為 OR-join（與 hooks shortlist 生產路徑一致），命中集可能變寬——例 `flock ledger` 修復前只回同時含兩詞的 slice，修復後含任一詞即命中；stopword 與單字元 latin token 會在 sanitize 時被丟棄；sanitize 後為空的查詢（純 stopword／符號）回空清單而非拋 SQL 錯誤。
