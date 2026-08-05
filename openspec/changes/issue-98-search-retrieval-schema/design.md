---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# Issue #98 `hippo search` FTS5 查詢 sanitize 設計

- 日期：2026-08-04
- Issue：[#98](https://github.com/hamanpaul/paulsha-hippo/issues/98)
- 狀態：已核可，待實作

## 背景

`hippo search` CLI 對含 `word:` 形式的查詢拋 `no such column`：FTS5 對 `MATCH ?` 綁定參數仍解析查詢語法。root cause 已以最小 sqlite3 腳本重現；證據見 spec（`docs/superpowers/specs/2026-08-04-issue-98-search-retrieval-schema-spec.md`）。

## Decisions

### D1：sanitize 接在 `moc/search.py::search()` 內、進 FTS5 前

`search()` 是 `hippo search` CLI 唯一入口，shortlist 路徑不經過它（走 `retrieval.py` 自己的查詢組裝、已 sanitize）。在 `search()` 內 sanitize 使未來任何新呼叫者自動安全，測試可直接對 `search()` 斷言。若實作發現 `search()` 另有已 sanitize 的呼叫者（雙重 sanitize 風險），退回 CLI 層（`moc/cli.py::run()`）並於 PR 說明。

### D2：重用 `retrieval.to_fts_query()`，單一 sanitizer 真理來源

不複製實作。簽名不合時以最小 adapter 呼叫，不 fork 邏輯。`search()` 內對所有查詢無條件套用，不設「已 canonical 形（`"tok" OR "tok"`）查詢」bypass：shortlist 生產路徑傳入的 canonical 查詢重 sanitize 為冪等（token 皆已通過長度／stopword 過濾、`OR` 本身是 stopword），bypass 只會引入第二條無測試守護的語意分支。

### D3：門檻為「既有測試涵蓋之語意零回歸」，非全輸入結果等價

`tests/test_moc_search.py` 既有斷言一條不改、全綠。但套用 `to_fts_query()` 對合法輸入**不是**結果等價：多詞查詢的比對語意由 FTS5 隱式 AND 收斂為 OR-join（例 `flock ledger` 修復前只命中同時含兩詞的 slice，修復後含任一詞即命中）；stopword 與單字元 latin token 被丟棄；sanitize 後為空的查詢回空 list 而非拋 SQL 錯誤。此語意成本為刻意接受——換取 CLI 與 hooks shortlist 共用單一 sanitizer 真理來源、行為一致；揭露於 changelog 碎片與 spec。

## Testing

- 新增：`"build: f5df394"`、`"tag:123"`、`"col:"` 不拋例外、回傳 list。
- 既有：全套 pytest、policy_check、openspec strict 全綠。
