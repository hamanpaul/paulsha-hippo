### Fixed
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。
- `build_index()` 並發安全：全程持 `runtime/locks/index-rebuild.lock`（阻塞式 flock）序列化所有 index writer（dream／rekey／retitle 任意呼叫路徑），temp DB 與 coverage 落盤改用 per-invocation 唯一暫存路徑——交錯的並發重建不再可能把對方未完成的索引發布成正式版。
- `build_index()` 發布視窗收口：coverage 併入同一顆 temp DB（`coverage` 表），與索引由**單次** `os.replace` 原子發布——coverage 寫入失敗（如 ENOSPC）或程序在兩步間終止，不再留下「新 DB＋舊/缺 coverage」的半發布狀態，失敗時舊索引與舊 coverage 完整保留；`retrieval.coverage.json` 改為發布成功後的派生輸出（衍生失敗僅記 warning，不推翻已發布索引），`hippo index verify` 改以 DB 內 coverage 為權威來源（無 coverage 表的舊版 DB 退回讀派生 JSON）。
- `build_index()` 對磁碟上重複 `slice_id`（naming dedup fail-soft 跳過後的殘留態）fail-soft：掃描迴圈先到先贏去重，後到者歸 `pool_excluded[duplicate-slice-id-on-disk]` 並記 warning——不再讓 `slice_meta` PK 的 `IntegrityError` 炸掉整批重建、連健康無關 slices 都退回舊索引；census 對賬鏡像同一規則（分佈對齊），`duplicate slice_id on disk` 仍由 `hippo index verify` 顯性回報。
- census 三方對賬的 fate/eligible 身份改以自身 line-based 獨立解析（`CensusEntry.slice_id/memory_layer`）為基準，並逐檔與 `fio.read` 交叉比對、任何 identity divergence 記入 problems——與 build_index 共用的 parser 誤判磁碟 ID（合法 YAML tag/anchor 如 `!!str sl-x`、或 parser bug）時，eligible 端與 DB 端不再拿到同一個錯 ID 而 false green（spec §3.2 防同源自證）。
- `build_index()` row 正規化嚴格驗證 `tags` 型別（必須為 list[str]；缺欄/null 視為空）＋逐檔分類全程包 per-slice 例外邊界：合法 YAML 的 `tags: [1]` 之類錯型歸 `invalid_frontmatter` 記路徑 warning、非預期分類例外亦只犧牲該檔——不再讓單一毒 slice 的 `TypeError` 炸掉整批重建、健康 slices 不發布或持續供應 stale index；census 雙寫同一 tags 型別規則（分佈對齊）。

### Added
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳、隨索引寫入 `retrieval.db` 的 `coverage` 表（權威來源，與索引成對原子發布）並派生 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。DB 反查驗到實際提供搜尋結果的兩張表（slice_meta ↔ slices_fts slice_id multiset 一對一 + FTS integrity-check）——FTS 缺行／幽靈行／重複行而 metadata 完整時不再 false green。
