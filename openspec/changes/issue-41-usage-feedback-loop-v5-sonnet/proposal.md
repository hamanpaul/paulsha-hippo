---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
---

# Issue #41 usage feedback loop 計畫提案

## 目的

- 建立 v5 planning authority，完整繼承 v4 已接受功能契約與機械化邊界；只清除 persisted-block 並切換 builder provider。
- 指定 builder：`claude/claude-sonnet-5`（effort high；一次初跑＋至多一次 repair）。

## supersede 與邊界

- v5 明確 supersede `issue-41-usage-feedback-loop-v4`，後續實作必以 v5 為 active change。
- `base reference`：`b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`
- 不允許改變 `issue #18` 的 read funnel、`applied` 分離、hook/ARGV/runtime safety。

## Requirements

以下核心要求完整保留：

1. `runtime/ledger/offered.jsonl` 與 `runtime/ledger/memory_usage.jsonl` 作為唯一來源。
2. 僅計入符合條件的 read：
   - 相同 `(tool, logical_session, slice_id)` 有 prior offer
   - `source = read`
   - 非 `applied`
   - `now - window <= ts <= now`
3. `read_count` / `last_read_at` 為 per-slice 統計，changelog 外不變更輸出欄位。
4. `base_score = bm25 - 0.1*link_weight`
5. `usage_boost = min(0.04, 0.01*log2(1 + read_count))`
6. `adjusted_score = base_score - usage_boost`
7. `read` 無正 boost 時走 legacy fast path；有 boost 時 stable key `(adjusted_score, base_score, slice_id)`；`base_score` 間隔超過 `0.04` 不 reverse。
8. 新 temp DB `slice_meta` 加 `read_count` / `last_read_at`，舊 DB 以 schema introspection fallback。
9. janitor retention base = `max(captured_at, active_since_ts, valid last_read_at)`，`superseded/source_invalid` 仍優先於 `ttl`，`read` 不可 re-activate。

## v5 新增 BLOCKER/MAJOR 閉環

- STREAMING：不得使用 `Path.read_text().splitlines()` 或一次讀入 list 的 ledger 解析。
- 資料品質：`malformed JSON`、`non-object`、缺少/空白 `tool/session/sl_id`、`invalid/future/out-of-window` 直接排除。
- 有界診斷：每個無法 cross-match 理由都記 fixed key counter；鍵集合有界。
- fail-soft：I/O、UTF-8、單行 parse 錯誤不應 abort rebuild/search/janitor。
- scanner 輸出：僅 counter >0 產 warning。
- module docstring：維持真實 `__doc__`，避免無關 formatting churn。
- 測試必測：malformed JSON、missing keys、invalid UTF-8/OSError、large ledger/no-read_text、no-zero-warning、legacy DB fallback、stable ranking、0.04 bound、janitor priority/retention、無 ledger mutation。

## 驗證門檻

- focused tests（由 builder 補齊）
- `pytest --ignore=tests/installed`
- installed-fixture wheel gate（於正確 built-wheel 環境）
- `openspec validate issue-41-usage-feedback-loop-v5-sonnet --strict`
- `openspec validate --all --strict`
- `python3 -m policy_check --repo .`
- `git diff --check`
- builder 實作與七件 authority 必須同一 commit。

## 審查鏈

- builder 後置 `agy/gemini-3.6-flash-high`（verification / code-review / adversarial）
- final exact candidate 由 `codex/gpt-5.6-luna(max)`（`model_reasoning_effort=max`）PASS
- Agy reviewer 必須先閱讀 frozen plan/todo/spec/design/tasks 與七件 authority（plan-only artifacts），再對照驗收結果。
- Agy 判準：未處置缺陷/缺口 FAIL；承認且有界列管殘餘風險不獨立 FAIL。
- 第二次 Claude build/repair 後仍有未處置 BLOCKER/MAJOR 即停在 `needs_human`，不得自動生成 v6。
