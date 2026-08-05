---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Issue #106 router deadline 提前 break 補記 skipped profile provenance 提案

## Why

`RouterState` 主迴圈（`paulsha_hippo/agent_profiles.py`，約 L888-905）在每個 profile 開始前檢查 `time.monotonic() - started >= session_deadline`；一旦成立就直接 `break`，剩下尚未嘗試、但本來 enabled 且 task_class 相符的 profile 完全不會留下任何 `attempts` 記錄。

生產實證：2026-08-04T02:00:33Z，session `claude-code:749f862e-0296-41f6-b7e5-79719075a32a` 理論上有 4 個 enabled profile（claude/codex/cg/local-vllm），但 `attempts` 只記到 3 筆——第 4 個 profile 連「ineligible」記錄都沒留，直接從證據鏈消失。此輪 park 把 AR-11 soak 窗口從 2/3 打回 0/3，是三個已知 reset 事故中唯一發生在窗口即將達標前一步的。

與 issue #89（呼叫次數預算未隨 chunk 數縮放）系出同源但非同一根因：#89 修的是「快速失敗的 profile 們互相排擠彼此的呼叫次數／時間」；本案是「第一個 profile 本身就跑到接近單次呼叫上限，加上第二個也吃掉部分時間，兩者相加即可吃光整條鏈預算」，導致迴圈甚至沒機會走到後段 profile 的 `eligible()` 判斷。

## What Changes

- `paulsha_hippo/agent_profiles.py::RouterState` 主迴圈因鏈預算耗盡提前 `break` 前——含 pre-attempt deadline check（reason `session_deadline`）與 attempt 中途 deadline / call budget 耗盡的 `budget` 底部 break（reason `session_budget`）兩條路徑——對剩餘 enabled 且 task_class 相符、且 circuit 未開路的 profile，逐一比照緊鄰的 `eligible=False` pattern 補一筆 `AgentRunResult`（`failure_category="ineligible"`）。
- 不新增 `FALLBACK_ON` / `classify_failure` 分類，不改變 dispatch 順序、deadline 算式、fallback 語意、park 行為——本次修復**只補觀測**；exhausted raise 的 `category` / `profile_id` / `exit_code` / `stderr` 錨定最後一筆真實 attempt（terminal 快照），不受合成 skip 記錄影響。
- 新增模擬慢 tier-1 鏈測試（`tests/test_external_agent_profiles.py`）、raise 內容回歸鎖、mid-attempt budget break 測試、circuit-open 不補記測試、`max_attempts` 溢出語意鎖，與預算充足時的回歸保護測試。
- 新增 `changelog.d/106-router-skip-provenance.md`（type: fix）。

## Impact

- 影響範圍：`paulsha_hippo/agent_profiles.py::RouterState`——全 repo 唯一每個 task_class、每個 session 都會經過的 dispatch 熱路徑。**絕對最小 diff**：只補 provenance 記錄。
- 風險：中——熱路徑改動，但變更範圍嚴格限定於「break 前補記」這一單點，不觸碰既有判斷式的時序與條件。
- 不影響：dispatch 順序、`session_deadline_seconds()` / `effective_max_agent_calls()` 算式、`FIXED_TIMEOUT_SECONDS`、`FALLBACK_ON` allowlist、park 觸發條件。
- Authority：`docs/superpowers/plans/2026-08-04-issue-106-router-skipped-profile-provenance.md`、spec/design 同名對（`docs/superpowers/specs/2026-08-04-issue-106-router-skipped-profile-provenance-{spec,design}.md`）。
