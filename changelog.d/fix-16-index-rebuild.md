### Fixed
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。
- `build_index()` 並發安全：全程持 `runtime/locks/index-rebuild.lock`（阻塞式 flock）序列化所有 index writer（dream／rekey／retitle 任意呼叫路徑），temp DB 與 coverage 落盤改用 per-invocation 唯一暫存路徑——交錯的並發重建不再可能把對方未完成的索引發布成正式版。

### Added
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳並原子落盤 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。DB 反查驗到實際提供搜尋結果的兩張表（slice_meta ↔ slices_fts slice_id multiset 一對一 + FTS integrity-check）——FTS 缺行／幽靈行／重複行而 metadata 完整時不再 false green。
