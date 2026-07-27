---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
---

# Issue #41 usage feedback loop v5 tasks

## Tasks

## RED→GREEN（順序）

1. [RED] 導入 v5 planning authority 套件（本 7 件）並維持 `status: accepted`。
2. [RED] 在 plan/proposal 明列 8 則 BLOCKER/MAJOR gate。
3. [RED] 明定函式 entrypoint 與資料流：
   - `paulsha_hippo/cli.py::_read_usage_jsonl`
   - `paulsha_hippo/cli.py::_load_usage_rows`
   - `paulsha_hippo/cli.py::_funnel_read_attribution`
   - `paulsha_hippo/cli.py::_usage_mark_applied`
   - `paulsha_hippo/moc/search.py::_build_index_locked`
   - `paulsha_hippo/moc/search.py::search`
   - `paulsha_hippo/janitor/scanner.py::run_scan`
   - `paulsha_hippo/janitor/rules.py::plan_scan`
4. [RED] 將新增測試邏輯定義為建置契約：
   - malformed JSON
   - missing keys
   - invalid UTF-8/OSError
   - large ledger/no-read_text
   - no-zero-warning
   - legacy DB fallback
   - stable ranking
   - 0.04 bound
   - janitor priority/retention
   - 無 ledger mutation
5. [GREEN] `claude/claude-sonnet-5` 以最小 patch 套用 v4 contracts + v5 lifecycle/provider 變更：`moc/search.py::search()` 排序鍵改為 `(adjusted_score, base_score, slice_id)` 三段式穩定鍵，修復 BLOCKER #7 RED regression（`tests/test_moc_search.py::test_search_stable_sort_uses_base_score_before_slice_id`）。
6. [GREEN] 交付前 run：
   - `pytest --ignore=tests/installed`
   - installed-fixture wheel gate（於正確 built-wheel 環境）
   - `openspec validate issue-41-usage-feedback-loop-v5-sonnet --strict`
   - `openspec validate --all --strict`
   - `python3 -m policy_check --repo .`
   - `git diff --check`
7. [GREEN] reviewer candidate 檢查：7 件 authority + 實作同 commit，且 OpenSpec strict validation 可識別 active change；Agy reviewer 需先讀取這份 frozen plan 與 7 件 authority。
8. [GREEN] 結案門檻：
   - `agy/gemini-3.6-flash-high` verification / code-review / adversarial
   - `codex/gpt-5.6-luna` final exact-head (`model_reasoning_effort=max`) PASS
   - Claude 初跑後最多一次 repair；仍未通過則 `needs_human`，不得自動建立 v6

## Agy 審查規則（v5）

- 只列 BLOCKER/MAJOR，且最多 8 條。
- 未處置缺陷/缺口為 FAIL。
- 已明文承認且影響有界、列管為殘餘風險不單獨 FAIL。
