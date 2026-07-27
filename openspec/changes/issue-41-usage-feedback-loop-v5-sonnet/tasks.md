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
5. [RED] direct closeout regressions：
   - [x] UTF-8 壞行前後合法 JSONL 行持續處理。
   - [x] future/out-of-window offer 排除並記 bounded counter。
   - [x] `(unknown)` tool/session/sl_id 全部拒絕。
   - [x] `_read_usage_jsonl` / `_load_usage_rows` 不回傳 ledger-wide lists。
   - [x] boosted exact tie 以 `(adjusted_score, base_score, slice_id)` 決勝。
   - [x] `usage_now/window_days` 可注入並傳至 aggregation。
   - [x] filesystem metadata I/O fail-soft。
   - [x] no-boost 同分維持 legacy stable order。
   - [x] future `last_read_at` 不延長 janitor TTL。
6. [GREEN] Codex native subagent 修復 RED；保留 #18 funnel/applied、
   hook/argv/runtime safety、legacy DB、flock/temp/atomic replace 與 janitor
   優先序。
7. [GREEN] 交付前 run：
   - `pytest --ignore=tests/installed`
   - installed-fixture wheel gate（於正確 built-wheel 環境）
   - `openspec validate issue-41-usage-feedback-loop-v5-sonnet --strict`
   - `openspec validate --all --strict`
   - `python3 -m policy_check --repo .`
   - `git diff --check`
8. [GREEN] 主 agent exact-head 檢查：7 件 authority、RED、實作與
   OpenSpec strict validation 一致。
9. [GREEN] 結案門檻：preflight-ci、PR current-head checks、review
   threads、mergeability 全綠；PR body `Closes #41`，merge 後 issue closed。

## current-head 審查規則（v5）

- 只列 BLOCKER/MAJOR，且最多 8 條。
- 未處置缺陷/缺口為 FAIL。
- 已明文承認且影響有界、列管為殘餘風險不單獨 FAIL。
