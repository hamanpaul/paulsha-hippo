---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
supersedes: issue-41-usage-feedback-loop-v4
base_sha: b4a317f9bfa38708eabbdb31e083dfc3b6e4c044
model: codex-native-subagent
created: 2026-07-27
---

# Issue #41 usage feedback loop v5 計畫

## 角色與範圍

- v5 是 `Issue #41` 的 planning authority；完整承接 v4 已接受契約，不增刪產品 scope。
- 使用者已於 2026-07-27 明確終止 Cortex lifecycle，改由主 Codex agent 以
  writing-plan → TDD → Codex native subagent → preflight-ci → PR → merge
  直接收尾；這只取代執行流程，不改產品契約。
- `base_sha` 固定為 `b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`。
- direct closeout 基底為 local candidate
  `6af69a418b334728c0deb50c601857fd1dbed236`，工作分支固定為
  `feature/41-issue-41-usage-feedback-loop-v5-sonnet`。
- builder 實作不得改變 `issue #18` 已完成 funnel、`applied` 行為、`hooks` 及 runtime safety。
- subagent 寫入邊界只允許本 worktree 內 issue #41 的 code/test/docs/changelog；
  主 agent 負責 plan、RED 證據、exact-head review、preflight、PR 與 merge。

## v4 承接＋v5 正式取代欄位

- `Issue #41` 契約完整承接 v4 的 `offered/read/applied` 基礎、usage feedback score 公式（`base/boost`）及全部機械化驗收要求。
- v5 正式 `supersede` `issue-41-usage-feedback-loop-v4`；不放寬大檔
  streaming、fail-soft、診斷鍵有界、scanner 無零值雜訊與 strict
  validation。

## v5 BLOCKER/MAJOR Closure（最多 8 條）

1. [BLOCKER] JSONL 來源讀取必須是逐行 iterator；禁止 `Path.read_text().splitlines()`，禁止將任一 ledger 全量載入 list。必須加入「`Path.read_text` 被 monkeypatch 成失敗」與「超大 ledger」二種回歸測試，驗證 bounded-memory 與逐行處理。
2. [BLOCKER] `malformed JSON`、`non-object`、缺少或空白 `tool/session/sl_id`、`invalid timestamp`、`future`、`out-of-window` 不得 cross-match；每種失敗必須更新固定 key 的 bounded counter，counter 集合有界，禁止無限 key。
3. [BLOCKER] offered/read 同時驗證時序與身份：`offered` 須先行，`read` 必須落在 `window` 內；`(unknown)` 或空字串不得形成可比對 identity，不能用作配對 key。
4. [BLOCKER] I/O、UTF-8、單行 parse error 必須 fail-soft；不得中止 index rebuild、search、janitor；ledger 不得被改寫。
5. [BLOCKER] scanner/janitor 警示只在 counter > 0 時輸出，禁止每次 scan 都輸出零值 noise。
6. [MAJOR] module docstring 必須保持 `__doc__` 真實欄位，不能因無關格式修正造成噪音 churn；changelog 文字需修正為準確實作語意。
7. [MAJOR] ranking 與衰減不變式固定：`usage_boost = min(0.04, 0.01 * log2(1 + read_count))`，無 boost 時走 legacy fast path；有 boost 時 stable key `(adjusted_score, base_score, slice_id)`；`base_gap > 0.04` 不可反轉。
8. [MAJOR] 實作與審查完成時需證明：focused tests、`pytest --ignore=tests/installed`、installed-fixture wheel gate、`openspec validate --all --strict`、`python3 -m policy_check --repo .`、`git diff --check` 全綠。

## 6af69a4 exact-candidate RED 缺口

以下缺口均已用 candidate 實測或靜態 call-chain 證實，不能以既有綠燈
suite 抵銷：

1. UTF-8 壞行會令 text buffer 在逐行 yield 前失敗，周邊合法 JSONL 行被
   一併丟棄；必須改成 binary line read + per-line UTF-8 decode，壞行計入
   fixed diagnostic 後繼續。
2. offered event 未檢查 future/out-of-window，過期 offer 仍可與窗口內 read
   cross-match。
3. `valid_identity()` 只拒絕 `(unknown)` tool，仍接受 `(unknown)` logical
   session 與 slice id。
4. boosted ranking 的第二排序欄位使用 raw BM25，而非權威指定的
   `base_score = bm25 - 0.1*link_weight`。
5. `_read_usage_jsonl()`、`_load_usage_rows()` 與 offered timestamp index 仍
   materialize ledger-wide lists；必須把原始 ledger rows 保持 iterator，僅
   保留輸出所需的 per-session/per-slice compact aggregates。
6. index build 使用不可注入的 `datetime.now()`；必須提供 keyword-only
   `usage_now` / `usage_window_days` 注入點，預設仍採 UTC now，讓 dream/index
   rebuild 與測試可重現。
7. `iter_ledger_events()` 在 `try` 外呼叫 `Path.exists()`；filesystem
   metadata I/O 失敗仍會逸出，違反 ledger 全流程 fail-soft。
8. 無正 boost 的 fast path 額外加入 raw BM25 / slice id tie-break，改變
   legacy base-score 同分時的 stable input order。
9. janitor 直接接受 future `last_read_at` 作為 TTL base，會令過期記憶被
   無限期保留，違反 malformed/future usage evidence fail-closed。

## Tasks

以下為 RED→GREEN 交付順序（本卡可直接交 builder）：

1. [RED] 以函式入口為核心維持 `usage aggregation → search scoring → janitor` 三段 anchor：
   - `paulsha_hippo/cli.py`: `_read_usage_jsonl`, `_load_usage_rows`, `_funnel_read_attribution`, `_usage_mark_applied`
   - `paulsha_hippo/moc/search.py`: `_build_index_locked`, `search`
   - `paulsha_hippo/janitor/scanner.py`: `run_scan`
   - `paulsha_hippo/janitor/rules.py`: `plan_scan`
2. [RED] 新增會在 `6af69a4` 失敗的 focused regressions：
   - UTF-8 壞行前後合法行都必須 yield；
   - future/out-of-window offer 不得歸因且 counter 可對帳；
   - `(unknown)` tool/session/sl_id 全部拒絕；
   - ledger loader 回傳 iterator 而非 row list；
   - boosted exact tie 以 `(adjusted_score, base_score, slice_id)` 決勝；
   - injected `usage_now/window_days` 精確傳到 aggregation；
   - filesystem metadata I/O fail-soft、no-boost legacy stable order、future
     janitor evidence fail-closed。
3. [GREEN] Codex subagent 只修 RED 所證 call chain，保留 #18 funnel 輸出、
   hook/argv safety、temp DB/flock/atomic replace 與 legacy DB fallback。
4. [GREEN] 更新 operator docs、`changelog.d`、`CHANGELOG.md [Unreleased]`；
   `VERSION` 不變。
5. [VERIFY] 先 focused tests，再一次完整
   `pytest --ignore=tests/installed`；另跑 built-wheel installed fixture、
   OpenSpec strict、policy、diff check 與 preflight-ci。
6. [DELIVER] PR 使用 zh-TW conventional title/body，body 包含 `Closes #41`；
   current-head checks、review threads 與 mergeability 全部通過後才 merge。

## v5 預期輸出

- `openspec validate issue-41-usage-feedback-loop-v5-sonnet --strict` 必過（active change）
- `openspec validate --all --strict` 必過
- `pytest --ignore=tests/installed` 必過；在已建置 candidate wheel 的正確環境補跑 installed-fixture
- `python3 -m policy_check --repo .` 與 `git diff --check` 必過
- PR-aware preflight-ci 必過，PR 以 merge commit 落到 `main` 並由
  `Closes #41` 關閉 issue。
