---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
---

# Issue #41 usage feedback loop v5 設計

## Decisions

以下決策為 v5 的 accepted design authority。

## 1) 流程與 Function anchors

- 讀取來源：`runtime/ledger/offered.jsonl`、`runtime/ledger/memory_usage.jsonl`。
- `usage aggregation` 與 `usage funnel` 錨點：
  - `paulsha_hippo/cli.py::_read_usage_jsonl`
  - `paulsha_hippo/cli.py::_load_usage_rows`
  - `paulsha_hippo/cli.py::_funnel_read_attribution`
  - `paulsha_hippo/cli.py::_usage_mark_applied`
- ranking 錨點：
  - `paulsha_hippo/moc/search.py::_build_index_locked`
  - `paulsha_hippo/moc/search.py::search`
- janitor 錨點：
  - `paulsha_hippo/janitor/scanner.py::run_scan`
  - `paulsha_hippo/janitor/rules.py::plan_scan`

## 2) v5 不能變更但可修正的行為

- 保留 `#18` usage funnel 行為。
- 保留 `applied` 與 `read` 分離。
- 保留 hook/argv/runtime safety。
- 保留既有 DB 發佈流程（flock、temp db、`os.replace`）與 no-in-place migration。

## 3) v5 聚合規則（逐行 + bounded diagnostics）

- `_read_usage_jsonl` 必須改為 line iterator，逐行 parse。
- `_load_usage_rows` 只回傳 raw offered / usage iterators 與 bounded
  diagnostics；不得回傳 ledger-wide offered/read/applied row lists。
- `offered`、`source=read` 及 `kind != applied` 先行過濾，但每條仍需檢查 identity 與時間窗。
- offered/read 配對條件同時要求：
  - `tool` 非空且非 `(unknown)`
  - `logical session` 非空
  - `sl_id` 非空
  - offered timestamp 先於 read
  - 時間窗合法
- 任一條件失敗則不 cross-match，並累加對應 fixed key counter。
- 所有錯誤（IO、decode、parse）都採 fail-soft，不阻斷後續處理；UTF-8
  以 binary line read 後逐行 decode，壞行前後的合法行仍須處理。
- path existence/stat 等 metadata I/O 也在 fail-soft 邊界內，不得於
  opening ledger 前逸出。
- offered index 每個 identity 只保留判斷 prior offer 所需的單一 timestamp，
  禁止 per-event timestamp list。

## 4) Ranking 設計（含 0.04 bound）

- `base_score = bm25 - 0.1 * link_weight`
- `usage_boost = min(0.04, 0.01 * log2(1 + read_count))`
- `adjusted_score = base_score - usage_boost`
- 全部 `usage_boost == 0` 時使用既有 legacy base-score one-key stable
  路徑，不新增 raw BM25 / slice id tie-break（速度與 ordering 相容）。
- 有 boost 時以 `(adjusted_score, base_score, slice_id)` 排序，且原始 base 相差 > 0.04 禁反轉。

## 5) Index / DB 相容

- 新 temp DB `slice_meta` 增加 `read_count INTEGER NOT NULL DEFAULT 0`、`last_read_at TEXT NULL`。
- 讀舊 DB 時以 schema introspection fallback。
- 任何 schema 缺欄位不做 in-place migration。
- `build_index(..., *, usage_now=None, usage_window_days=30)` 將可重現時間
  基準傳入 usage aggregation；`None` 才解析為目前 UTC。

## 6) Janitor 保持可觀測性

- `run_scan` 將 stats 傳遞給 `plan_scan`；`plan_scan` 以 `superseded`、`source_invalid`、`ttl` 優先級；`ttl_base` 與 `source` 寫入 decision detail。
- `read` 不得讓 decayed/superseded/source-invalid slice 重新 active。
- `last_read_at > now` 視為無效 usage evidence，不得延長 TTL。
- 當 `read_count == 0` 不得產生負向 decay。
- scan 警示僅輸出在 counter > 0。

## 7) 變更可測邏輯（交付給 builder）

- malformed JSON / non-object / missing key / window invalid 的固定 counter 與行為。
- 大 ledger + `Path.read_text` monkeypatch 回歸。
- no-zero warning。
- legacy DB fallback。
- stable ranking 與 0.04 bound。
- raw ledger loader 的 iterator contract 與 injected time/window。
- janitor priority/retention 與 future evidence fail-closed。
- 不改寫 ledger。

## 8) 交付後治理

- OpenSpec strict validation 以 `issue-41-usage-feedback-loop-v5-sonnet` 作為 active change。
- exact candidate head 必須包含 authority、RED regressions 與實作；
  reviewer checkout 需可直接閱讀 frozen plan 與權威文件。
- current-head reviewer 必須先閱讀 frozen plan 與權威文件；無法處置缺口
  視為 FAIL。
