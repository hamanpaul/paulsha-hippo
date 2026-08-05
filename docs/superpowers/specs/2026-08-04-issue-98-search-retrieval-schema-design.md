---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# `hippo search` FTS5 查詢 sanitize（issue #98）設計

- 日期：2026-08-04
- Issue：[#98](https://github.com/hamanpaul/paulsha-hippo/issues/98)
- 狀態：已核可，待實作

## 背景

證據與 root cause 見 spec。本設計唯一的問題是「sanitizer 接在哪一層」。

## Decisions

### D1：接在 `hippo search` CLI 進入 FTS5 前的最小語意處

候選：(a) `moc/cli.py::run()` 傳入前先 sanitize；(b) `moc/search.py::search()` 內部 sanitize。

**選 (b)**：`search()` 是 CLI 唯一入口點且未被 shortlist 路徑共用（shortlist 走 `retrieval.py` 自己的查詢組裝，已 sanitize）；在 `search()` 內 sanitize 使任何未來新呼叫者自動安全，且測試可直接對 `search()` 斷言。若實作時發現 `search()` 尚被其他已 sanitize 的路徑呼叫（雙重 sanitize 語意風險），退回 (a) 並在 PR 說明。

### D2：重用 `retrieval.to_fts_query()`，不複製貼上

單一 sanitizer 真理來源。若簽名不合（如需要 tokenize 選項），以最小 adapter 呼叫，不 fork 邏輯。

### D3：語意零回歸為硬門檻

`tests/test_moc_search.py` 既有斷言一條都不許改。sanitize 後的查詢對「本來就合法」的輸入必須產生等價結果——`to_fts_query()` 在 shortlist 路徑的既有行為已證明這點。

## Testing

- 新增：column-filter 形（`build: f5df394`／`tag:123`／`col:`）不拋例外。
- 既有：全套綠。
