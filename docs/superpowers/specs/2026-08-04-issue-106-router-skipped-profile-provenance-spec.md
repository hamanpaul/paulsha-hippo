---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Router deadline 提前 break 遺失 profile provenance（issue #106）規格

## Problem and Outcome

`RouterState` 主迴圈（`paulsha_hippo/agent_profiles.py::RouterState`，約 L888-905）在每個 profile 開始前檢查 `time.monotonic() - started >= session_deadline`；一旦成立就直接 `break`，不會為剩下尚未嘗試、但本來 enabled 且 task_class 相符的 profile 留下任何 `attempts` 記錄。

生產實證（2026-08-04T02:00:33Z，session `claude-code:749f862e-0296-41f6-b7e5-79719075a32a`）：claude 跑到單次呼叫 300s 上限（`FIXED_TIMEOUT_SECONDS`）逼近後才判 timeout，codex 接著也失敗，兩者合計吃掉大半 600s 鏈預算（`FIXED_SESSION_DEADLINE_SECONDS`）。整輪理論上有 claude/codex/cg/local-vllm 4 個 enabled profile，但 `attempts` 只記到 3 筆——第 4 個 profile（local-vllm）連「ineligible」記錄都沒有，直接從證據鏈消失。此次 park 把 AR-11 soak 窗口從 2/3 直接打回 0/3，是目前三個已知 reset 事故中唯一發生在窗口即將達標前一步的。

Root cause 與既有修復（issue #89 系列：把單次呼叫 timeout 與整鏈預算拆開為 `FIXED_SESSION_DEADLINE_SECONDS`）解決的是不同子情境：#89 修的是「快速失敗的 profile 們互相排擠彼此的呼叫次數／時間」；本案是「**第一個 profile 本身就跑到接近單次上限**，加上第二個 profile 也吃掉一部分時間，兩者相加即可吃光整條鏈預算」，導致迴圈甚至沒機會走到第 3、4 個 profile 的 `eligible()` 判斷。

預期結果：`RouterState` 因 `session_deadline` 提前 `break` 時，對所有本應被嘗試、但因鏈預算耗盡而未嘗試的 enabled+task_class 相符 profile，比照緊鄰的 `eligible=False` pattern 各補一筆 provenance 記錄，使 `attempts_detail` 不再「憑空少 profile」；下游只能靠 attempts 數量倒推、不利除錯與稽核的現況因此消除。此修復**只補觀測**，不改變 dispatch 順序、deadline 算式、fallback 語意或 park 行為——這是全 repo 唯一每個 task_class、每個 session 都會經過的 dispatch 熱路徑，任何行為面改變都超出本次修復範圍。

## Goals

- G1：`RouterState` 因 `session_deadline` 提前 `break` 時，為每一個原本 enabled 且 `task_class` 相符、但因預算耗盡而未被嘗試的 profile，各補一筆 provenance 記錄（樣式比照緊鄰的 `eligible=False` / `failure_category="ineligible"` pattern）。
- G2：新增記錄的 reason 沿用既有 ineligible 分類體系（例如 `session_deadline`），不新增 `FALLBACK_ON` / `classify_failure` 分類，不擴大 fallback allowlist。
- G3：預算充足、迴圈正常走完全部 profile 的既有情境，記錄行為與現行完全一致（零回歸）。
- G4：全套 pytest、`policy_check`、`openspec validate --all --strict` 綠。

## Non-goals

- 不改 dispatch 順序、`session_deadline_seconds()` / `effective_max_agent_calls()` 算式、fallback 語意、park 行為。
- 不放寬 `FIXED_TIMEOUT_SECONDS`、不動 `FALLBACK_ON` allowlist。
- 不評估「鏈預算是否該考慮已知 profile 數 × 各自最大耗時」這類演算法層改動（issue 中列為未定調的可能方向，非本次範圍）。

## Acceptance

- 模擬鏈 `[claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]`：斷言 break 後 `attempts`/provenance 含所有 enabled 且 task_class 匹配的 profile，被跳過者各有一筆記錄。
- 預算充足時的既有測試全綠（回歸保護）。
- 對應 accepted plan：`docs/superpowers/plans/2026-08-04-issue-106-router-skipped-profile-provenance.md`。
