### Fixed

- `moc.search.search()` 排序鍵改為 `(adjusted_score, base_score, slice_id)` 三段式穩定鍵；先前僅以單一 `bm25 - 0.1*link_weight` 鍵排序，usage boost 造成同分時退回插入序而非以 `base_score` 決勝，違反 issue #41 v5 排序不變式（BLOCKER #7）。
- usage ledger（`offered.jsonl`／`memory_usage.jsonl`）解析改為逐行 streaming iterator（`paulsha_hippo/ledger/usage.py`），不再以 `Path.read_text().splitlines()` 一次載入整檔；I/O、UTF-8、單行 parse 錯誤 fail-soft 且不改寫 ledger（BLOCKER #1/#4）。
- malformed JSON、非 object 行、缺漏/空白 `tool`／`session`／`sl_id`、無法解析／未來／窗口外時間戳皆不得 cross-match，改記入固定鍵集合的有界診斷 counter（BLOCKER #2/#3）。

### Added

- `moc.search` 新增 usage-boost 排序：`slice_meta` 增加 `read_count`／`last_read_at` 欄位，`search()` 以 schema introspection 對舊索引 fallback 為 legacy 6 欄查詢；`usage_boost = min(0.04, 0.01*log2(1+read_count))`，無正 boost 時走 legacy fast path，`base_score` 間距超過 `0.04` 時不會被 boost 反轉。
- janitor retention base 改為 `max(captured_at, active_since_ts, valid last_read_at)`，`ttl_expired` 事件 detail 新增 `ttl_base`／`source`；`read` 只延長已 active 記錄的保留時鐘，不會重新啟用已 decayed 的 slice（`superseded`／`source_invalid` 優先序不變）。scanner 新增 usage ledger 診斷警告，僅在對應 counter > 0 時輸出。
