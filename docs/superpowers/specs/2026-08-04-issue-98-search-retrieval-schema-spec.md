---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# `hippo search` FTS5 查詢 sanitize（issue #98）規格

## Problem and Outcome

`hippo search` CLI 對現行 retrieval.db 查詢含 `word:` 形式的字串（例如 `build: f5df394`）時拋 `no such column: build`（AR-05 佐證期間捕獲，issue #98）。

Root cause 已以最小 sqlite3 腳本重現：SQLite FTS5 對 `MATCH ?` 的綁定參數仍會解析查詢語法，`word:` 被當成 column-filter 前綴，欄位不存在即報 `sqlite3.OperationalError: no such column`。

Repo 內已有做法完全正確的 sanitizer：`paulsha_hippo/retrieval.py::to_fts_query()`。hooks 的 prompt-time shortlist 路徑（`hooks/_shortlist_common.py`）已使用它，因此不受影響；只有 `hippo search` CLI 入口（`paulsha_hippo/moc/cli.py::run()` → `paulsha_hippo/moc/search.py::search()`）把使用者查詢原文直送 FTS5。

預期結果：`hippo search` 對任意使用者查詢字串（含 column-filter 形、引號、特殊字元）不再拋 SQL 層例外；回傳正常（可為空）的結果列表；既有測試涵蓋之排序與比對語意零回歸。注意這**不是**全輸入結果等價：套用 `to_fts_query()` 後，多詞查詢的比對語意由 FTS5 隱式 AND 收斂為 OR-join（與 hooks shortlist 路徑一致，命中集可能變寬）、stopword／單字元 latin token 會被丟棄、sanitize 後為空的查詢回空清單而非拋錯——此為刻意接受的語意成本，換取與 shortlist 共用單一 sanitizer。

## Goals

- G1：`hippo search` CLI 路徑套用 `retrieval.to_fts_query()`（重用，不複製實作）。
- G2：`tests/test_moc_search.py` 既有測試全綠（既有測試涵蓋之排序／比對語意零回歸）。
- G3：新增回歸測試涵蓋 column-filter 形查詢。

## Non-goals

- 不改 FTS5 schema、不改 shortlist/recall 主路徑（本來就正確）、不新增查詢語法功能。

## Acceptance

- 對 `search()`（CLI 同路徑）餵 `"build: f5df394"`、`"tag:123"`、`"col:"` 不拋例外，回傳 list。
- 全套 pytest、policy_check、openspec strict 全綠。
