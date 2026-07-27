---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
supersedes: issue-41-usage-feedback-loop-v4
base_sha: b4a317f9bfa38708eabbdb31e083dfc3b6e4c044
model: claude-sonnet-5
created: 2026-07-27
---

# Issue #41 usage feedback loop v5 計畫

## 角色與範圍

- v5 是 `Issue #41` 的 planning authority；完整承接 v4 已接受契約，不增刪產品 scope。
- 本任務為 planning-only：**除七件 authority 外，不得修改任何其他檔案**；後續 builder 依本 plan 修改 code/test/docs/changelog。
- `base_sha` 固定為 `b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`。
- builder 實作不得改變 `issue #18` 已完成 funnel、`applied` 行為、`hooks` 及 runtime safety。
- v5 只修正 lifecycle 身分與 builder provider：v4 的 source-owner 競態留下 persisted block；依使用者指示改由 `claude/claude-sonnet-5` 執行 build，Agy 與 Luna gate 不變。
- 成本護欄：Claude builder 只允許一次初跑與至多一次 repair；第二次仍有未處置 BLOCKER/MAJOR 時停在 `needs_human`，不得自動生成 v6。

## v4 承接＋v5 正式取代欄位

- `Issue #41` 契約完整承接 v4 的 `offered/read/applied` 基礎、usage feedback score 公式（`base/boost`）及全部機械化驗收要求。
- v5 正式 `supersede` `issue-41-usage-feedback-loop-v4`；變更原因僅為清除 persisted-block 並切換 builder provider，不放寬大檔 streaming、fail-soft、診斷鍵有界、scanner 無零值雜訊與 strict validation。

## v5 BLOCKER/MAJOR Closure（最多 8 條）

1. [BLOCKER] JSONL 來源讀取必須是逐行 iterator；禁止 `Path.read_text().splitlines()`，禁止將任一 ledger 全量載入 list。必須加入「`Path.read_text` 被 monkeypatch 成失敗」與「超大 ledger」二種回歸測試，驗證 bounded-memory 與逐行處理。
2. [BLOCKER] `malformed JSON`、`non-object`、缺少或空白 `tool/session/sl_id`、`invalid timestamp`、`future`、`out-of-window` 不得 cross-match；每種失敗必須更新固定 key 的 bounded counter，counter 集合有界，禁止無限 key。
3. [BLOCKER] offered/read 同時驗證時序與身份：`offered` 須先行，`read` 必須落在 `window` 內；`(unknown)` 或空字串不得形成可比對 identity，不能用作配對 key。
4. [BLOCKER] I/O、UTF-8、單行 parse error 必須 fail-soft；不得中止 index rebuild、search、janitor；ledger 不得被改寫。
5. [BLOCKER] scanner/janitor 警示只在 counter > 0 時輸出，禁止每次 scan 都輸出零值 noise。
6. [MAJOR] module docstring 必須保持 `__doc__` 真實欄位，不能因無關格式修正造成噪音 churn；changelog 文字需修正為準確實作語意。
7. [MAJOR] ranking 與衰減不變式固定：`usage_boost = min(0.04, 0.01 * log2(1 + read_count))`，無 boost 時走 legacy fast path；有 boost 時 stable key `(adjusted_score, base_score, slice_id)`；`base_gap > 0.04` 不可反轉。
8. [MAJOR] 實作與審查完成時需證明：focused tests、`pytest --ignore=tests/installed`、installed-fixture wheel gate、`openspec validate --all --strict`、`python3 -m policy_check --repo .`、`git diff --check` 全綠。

## Tasks

以下為 RED→GREEN 交付順序（本卡可直接交 builder）：

1. [RED] 規格階段：確認 v4 七件 authority 與 main 相關程式後，建立內容等價的 v5 七件 authority。
2. [RED] 以函式入口為核心建 `usage aggregation → search scoring → janitor` 三段 anchor 清單：
   - `paulsha_hippo/cli.py`: `_read_usage_jsonl`, `_load_usage_rows`, `_funnel_read_attribution`, `_usage_mark_applied`
   - `paulsha_hippo/moc/search.py`: `_build_index_locked`, `search`
   - `paulsha_hippo/janitor/scanner.py`: `run_scan`
   - `paulsha_hippo/janitor/rules.py`: `plan_scan`
3. [RED] 明訂診斷鍵全集（例如 `json_decode_error`、`non_object`、`missing_tool`、`missing_session`、`missing_sl_id`、`invalid_ts`、`future_event`、`window_older`、`window_future`、`read_without_offered`），要求有界、可對帳且不無限擴張。
4. [RED] 在任務文檔中明確指定測試集合：malformed JSON、missing keys、invalid UTF-8/OSError、large ledger/no-read_text、no-zero-warning、legacy DB fallback、stable ranking、0.04 bound、janitor priority/retention、無 ledger mutation。
5. [GREEN] builder 必須將「這 7 件 v5 authority」與實作變更放在同一個 commit。
6. [GREEN] Agy reviewer 必須先完整閱讀此 frozen plan 與七件 authority，然後再核對實作與測試結果。
7. [GREEN] 實作階段：Claude Sonnet 5 builder 以最小 patch 實現 v5 requirements；保留 v4 已接受結果，不重複變更非目標模組。
8. [GREEN] `changelog.d` 與 `CHANGELOG.md [Unreleased]` 補齊；`VERSION` 僅在 release label 下調整。

## v5 預期輸出

- `openspec validate issue-41-usage-feedback-loop-v5-sonnet --strict` 必過（active change）
- `openspec validate --all --strict` 必過
- `pytest --ignore=tests/installed` 必過；在已建置 candidate wheel 的正確環境補跑 installed-fixture
- `python3 -m policy_check --repo .` 與 `git diff --check` 必過
