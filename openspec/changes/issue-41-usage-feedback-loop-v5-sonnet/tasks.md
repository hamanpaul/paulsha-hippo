---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
plan_ref: docs/superpowers/plans/2026-07-27-issue-41-usage-feedback-loop-v5-sonnet.md
---

# Issue #41 usage feedback loop v5 tasks

- [ ] [BLOCKER] JSONL 來源讀取逐行 iterator，禁止 `Path.read_text().splitlines()`；
  bounded-memory 與 `Path.read_text` monkeypatch-fail 兩種回歸測試
- [ ] [BLOCKER] malformed JSON／non-object／缺鍵或空白 tool-session-sl_id／invalid
  timestamp／future／out-of-window 不得 cross-match；每種失敗更新固定 key 的
  有界 counter
- [ ] [BLOCKER] offered/read 同時驗證時序（offered 先行、read 落在 window 內）與
  身份；`(unknown)` 或空字串不得形成可比對 identity
- [ ] [BLOCKER] I/O、UTF-8、單行 parse error 必須 fail-soft，不得中止 index
  rebuild/search/janitor，ledger 不得被改寫
- [ ] [BLOCKER] scanner/janitor 警示只在 counter > 0 時輸出，禁止零值 noise
- [ ] [MAJOR] module docstring 保持真實欄位；changelog 文字修正為準確實作語意
- [x] [MAJOR] ranking 與衰減不變式：`usage_boost = min(0.04, 0.01 * log2(1 +
  read_count))`；無 boost 時走 legacy fast path；有 boost 時 stable key 為
  `(adjusted_score, base_score, slice_id)`；`base_gap > 0.04` 不可反轉。
  — RED：`tests/test_moc_search.py::SearchTests::test_search_stable_sort_uses_base_score_before_slice_id`
  重現目前 `paulsha_hippo/moc/search.py::search` 只用單一
  `bm - 0.1 * link_weight` 排序鍵、無 `(adjusted_score, base_score, slice_id)`
  tie-break，同分時退回插入序而非 base_score 排序。
- [ ] [MAJOR] focused tests、`pytest --ignore=tests/installed`、installed-fixture
  wheel gate、`openspec validate --all --strict`、`python3 -m policy_check
  --repo .`、`git diff --check` 全綠證明
