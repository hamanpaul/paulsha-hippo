---
status: accepted
work_item: issue-98-search-retrieval-schema
---

# `hippo search` FTS5 查詢 sanitize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試、看它以正確理由失敗、再實作。

**Goal:** `hippo search` CLI 對含 `word:` 形式的查詢（如 `build: f5df394`）不再拋 `no such column: build`；修法是把 CLI 路徑接上既有的 sanitizer，不新增任何查詢語法設計。

**Root cause（已驗證）:** SQLite FTS5 對 `MATCH ?` 綁定參數仍會解析查詢語法；查詢字串含 `word:` 時 `word` 被當 column-filter，欄位不存在即報 `no such column`。`paulsha_hippo/retrieval.py::to_fts_query()` 已是正確 sanitizer，hooks 的 shortlist 路徑（`hooks/_shortlist_common.py`）已在用；只有 `moc/cli.py::run()`（`hippo search` 入口）呼叫 `moc/search.py::search()` 時把 `args.query` 原文直送 FTS5。

**Target branch:** `feature/98-search-fts-sanitizer`

## Global Constraints

- 語言：PR 標題、內文、commit message、changelog 碎片一律 zh-tw。
- TDD：先寫測試看 RED 再實作。
- 交付治理：同一 PR 需 `changelog.d/<slug>.md` 碎片；`python3 -m pytest -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠；PR body 用 repo PR template 且 checklist 全勾；body 以 `Closes #98` 關閉 issue。
- 全套測試請在非巢狀 sibling worktree 跑；repo 巢狀 worktree 下 `tests/test_project_resolver.py` 有 2 個環境假失敗屬已知訊號。
- 不可回歸 `tests/test_moc_search.py` 既有排序/比對語意。
- commit 前 `rm -rf .psc_tmp`，不要用 `git add -A`。

## Task 1: CLI 查詢接上 sanitizer

**檔案**：`paulsha_hippo/moc/search.py`（或 `paulsha_hippo/moc/cli.py`，擇語意最小處）、`tests/test_moc_search.py`

- [ ] 先寫測試：對 `search()` 餵 `"build: f5df394"`、`"tag:123"`、單獨 `"col:"` 等 column-filter 形查詢，斷言不拋 `sqlite3.OperationalError`／`SearchIndexError`，回傳 list（可為空）。
- [ ] 先寫測試：正常關鍵字查詢（既有測試語料）結果與修復前一致。
- [ ] 實作：在 `hippo search` 進入 FTS5 前套用 `retrieval.to_fts_query()`（重用，不複製貼上實作）。
- [ ] 全套測試綠；新增 `changelog.d/98-search-fts-sanitizer.md`（type: fix）。
