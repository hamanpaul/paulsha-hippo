---
type: fix
---
- 修 issue #98：`hippo search` 對含冒號的查詢報 `search failed: no such column: build`。根因是 FTS5 對 `MATCH ?` 的綁定參數**仍會解析查詢語法**——`word:` 形式被當成 column-filter 前綴，該欄不存在即 `OperationalError`；裸露的 `AND`／`*`／`NEAR/`／未閉合引號同理是語法錯誤。本 issue 曾於 2026-08-05 關閉但無對應 commit 落地，缺陷在部署 build `1299fa1` 上仍完整重現，故重開修復。
- 修法重用既有 sanitizer `paulsha_hippo/retrieval.py::to_fts_query()`，不新設計：prompt-time shortlist 路徑（`paulsha_hippo/hooks/_shortlist_common.py:377`）本來就走它，這正是 hooks 路徑不受此 bug 影響的原因；缺的是 `hippo search` CLI 這一條。
- 消毒收在 `paulsha_hippo/moc/search.py::search()` 內而非 `moc/cli.py`：`search()` 的生產呼叫點目前僅 CLI 一處，收在函式內讓未來新增的 caller 也繞不過同一個收口點，而不是每個入口各自記得消毒。
- 純 stopword／單字元輸入經 sanitizer 後為空字串，空 MATCH 本身是 FTS5 語法錯誤，故在開啟 DB 之前早退回傳 `[]`。
- **語意變更（已於 docstring 標註）**：`to_fts_query()` 逐詞加引號後以 `OR` 串接，故多詞查詢由 FTS5 預設的隱含 AND 變為 OR，命中集合變寬，實際先後仍由既有 bm25 + link_weight + usage boost 排序決定。單詞查詢行為不變，既有排序／比對測試全數未動且通過。
- 新增測試：含冒號查詢（`build: f5df394`）回傳正常結果而非拋錯；`AND* (bad`／未閉合引號／`NEAR/` 三種裸露運算子不再炸開；content-less 查詢在**開 DB 之前**早退（以 `sqlite3.connect` 未被呼叫斷言，而非只斷言回傳空集合——後者在修復前後皆成立、無法辨識缺陷）。
