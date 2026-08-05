---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Router deadline 提前 break 遺失 profile provenance（issue #106）設計

- 日期：2026-08-04
- Issue：[#106](https://github.com/hamanpaul/paulsha-hippo/issues/106)
- 狀態：已核可，待實作

## 背景

證據與 root cause 見 spec（`docs/superpowers/specs/2026-08-04-issue-106-router-skipped-profile-provenance-spec.md`）。本設計唯一要決定的是：break 前要補記什麼樣的 provenance、reason 怎麼分類、以及如何確保零行為回歸。

## Decisions

### D1：break 前，比照緊鄰的 `eligible=False` pattern，逐一為剩餘 profile 補一筆 skipped provenance

`RouterState` 主迴圈既有的 `eligible=False` 分支（約 L906-924）已經示範了正確樣式：對「enabled 且 task_class 相符，但無法執行」的 profile 追加一筆 `AgentRunResult(..., model_verification="unavailable", failure_category="ineligible", stderr=sanitize_stderr(reason), ...)`，`elapsed_seconds=0.0`、不消耗 agent call。`time.monotonic() - started >= session_deadline` 觸發 `break` 時，改為先對 `self.profiles` 中尚未走到、且 `profile.enabled and self.task_class in profile.task_classes` 為真的每一個 profile，依同一 `AgentRunResult` 建構樣式各 append 一筆記錄，再執行原本的 `break`。已經因為 circuit breaker（`_circuit_open_until`）而被 `continue` 跳過的 profile 維持原樣不受影響——本決策只處理「deadline 提前 break」這一種消失路徑。

### D2：reason 沿用既有 ineligible 分類體系，不新增 fallback category

`profile.eligible()` 已有 `session_size` / `context_budget` / `disabled` / `task_class` 等 reason 字串，`failure_category` 統一是 `"ineligible"`（不是 `classify_failure()` 的 `FALLBACK_ON` 分類）。新記錄比照這個既有體系，補一個新的 reason 字串（例如 `"session_deadline"`）標示「因鏈預算耗盡而未被嘗試」，`failure_category` 仍填 `"ineligible"`。**不**把這個 reason 加進 `FALLBACK_ON` / `ALLOWED_FALLBACK_CATEGORIES`——它不是一個可以觸發 fallback 判斷的失敗分類，只是 provenance 上的一個標記值，`classify_failure()` 的邏輯與 allowlist 維持零改動。

### D3：行為面零改變，只補觀測

本次修復不得改變：dispatch 順序（`for profile in self.profiles` 迭代順序不變）、`session_deadline_seconds()` / `effective_max_agent_calls()` 算式、`FIXED_TIMEOUT_SECONDS` / `FIXED_SESSION_DEADLINE_SECONDS`、fallback 語意（`FALLBACK_ON` allowlist 不動）、park 行為（`_raise_exhausted` 的觸發條件與內容不變，只是 `attempts` 列表在傳入前多了幾筆 skipped 記錄）。新增的 provenance 記錄消耗 `elapsed_seconds=0.0` 且不遞增 `calls`，因此不影響 `len(attempts) >= self.max_attempts` 或 `calls >= session_call_budget` 的既有判斷時序——這些記錄是在迴圈即將 `break` 之後、函式返回之前一次性補齊，不會讓迴圈多繞一圈。預算充足、迴圈正常走到最後一個 profile 而不觸發 deadline break 的既有情境，程式路徑完全不經過這段新增程式碼，因此零回歸。

## Testing

- 新增：模擬鏈 `[claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]`，斷言 break 後 `attempts` 對應所有 enabled+task_class 相符 profile，被跳過者各有一筆 `failure_category="ineligible"` 記錄。
- 新增：預算充足時記錄行為與現行完全一致（回歸保護鎖）。
- 既有：`tests/test_external_agent_profiles.py` 全套綠；全套 pytest、`policy_check`、`openspec validate --all --strict` 綠。
