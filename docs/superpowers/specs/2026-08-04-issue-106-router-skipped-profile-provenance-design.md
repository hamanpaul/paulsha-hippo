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

### D1：兩條 break 路徑都比照緊鄰的 `eligible=False` pattern，逐一為剩餘 profile 補一筆 skipped provenance；circuit-open 的剩餘 profile 不補記

`RouterState` 主迴圈既有的 `eligible=False` 分支已經示範了正確樣式：對「enabled 且 task_class 相符，但無法執行」的 profile 追加一筆 `AgentRunResult(..., model_verification="unavailable", failure_category="ineligible", stderr=sanitize_stderr(reason), ...)`，`elapsed_seconds=0.0`、不消耗 agent call。補記邏輯抽成 `_record_skipped_profiles()` helper，覆蓋兩條「鏈預算耗盡致 profile 消失」的路徑：

1. **Top-of-loop deadline break**：`time.monotonic() - started >= session_deadline` 成立時，先對 `self.profiles` 中尚未走到的剩餘 profile 補記（reason `session_deadline`），再執行原本的 `break`。
2. **Bottom break on `budget`**：deadline 或 call budget 在 attempt 中途耗盡時，chunk 迴圈 raise `category="budget"`（NON_FALLBACK → 底部 break）——多 chunk session 為生產常態，此路徑真實可達。此時同樣對剩餘 profile 補記（reason `session_budget`）。其他 non-fallback category（如 `policy`）是 per-profile 的鏈終止決策，不屬於「鏈預算耗盡」，維持原樣不補記。

**circuit-open 這一格的明確決策**：補記時逐一檢查 `_circuit_open_until`——circuit 尚開著的剩餘 profile **不補記**。主迴圈對 circuit-open profile 本來就是無聲 `continue`（零記錄），該 profile 就算預算充足也不會被嘗試；補一筆 `session_deadline` 會造成同一狀態在兩條路徑下 provenance 不一致、且記錄語意失真。已經因 circuit breaker 被 `continue` 跳過（在迴圈行進中）的 profile 同樣維持原樣不受影響。

### D2：reason 沿用既有 ineligible 分類體系，不新增 fallback category

`profile.eligible()` 已有 `session_size` / `context_budget` / `disabled` / `task_class` 等 reason 字串，`failure_category` 統一是 `"ineligible"`（不是 `classify_failure()` 的 `FALLBACK_ON` 分類）。新記錄比照這個既有體系，補兩個新的 reason 字串：`"session_deadline"`（pre-attempt deadline check 觸發）與 `"session_budget"`（attempt 中途 deadline / call budget 耗盡觸發），標示「因鏈預算耗盡而未被嘗試」，`failure_category` 仍填 `"ineligible"`。**不**把這些 reason 加進 `FALLBACK_ON` / `ALLOWED_FALLBACK_CATEGORIES`——它們不是可以觸發 fallback 判斷的失敗分類，只是 provenance 上的標記值，`classify_failure()` 的邏輯與 allowlist 維持零改動。

### D3：行為面零改變，只補觀測——raise 內容錨定 terminal 真實 attempt

本次修復不得改變：dispatch 順序（`for profile in self.profiles` 迭代順序不變）、`session_deadline_seconds()` / `effective_max_agent_calls()` 算式、`FIXED_TIMEOUT_SECONDS` / `FIXED_SESSION_DEADLINE_SECONDS`、fallback 語意（`FALLBACK_ON` allowlist 不動）、park 行為。

**park 行為不變的機制**：`_raise_exhausted` 原本從 `attempts[-1]` 取 raise 的 `category` / `profile_id` / `exit_code` / `stderr`，但補記後列表尾端可能是合成的 skip 記錄——若直接沿用，timeout 會被翻成 ineligible，下游 `llm_promoter` 的 category 映射（transient ↔ backend_unavailable）與 park 落盤的 failure_category 全部跟著翻。因此 `run_session` 全程追蹤 `terminal`（最後一筆**真實** attempt 記錄：實際執行過的 attempt 或既有 `eligible=False` ineligible 記錄），並顯式傳入 `_raise_exhausted(attempts, terminal)`；`_raise_exhausted` 未收到顯式參數時預設仍取 `attempts[-1]`（pre-#106 呼叫形狀）。`self.attempts` 保留含 skip 的完整列表供 provenance，raise 的內容一律取自 terminal——provenance 變多，錯誤本身不變。

**max_attempts 上限的明文語意**：skip 記錄是在迴圈已決定 `break` 之後一次性補齊，不經過 top-of-loop 的 `len(attempts) >= self.max_attempts` 判斷，因此 **`attempts` 列表長度可以超過 `max_attempts`**（例：max_attempts=3、4-profile 鏈在第 1 筆後 deadline break → 1 真實 + 3 skip = 4 筆）。這是預期語意，非 bug：skip 記錄純屬 provenance，不消耗 agent call；park 訊息「after N bounded chunk attempt(s)」的 N 因此把未曾執行的 profile 也計入——這是「attempt chain 必須含所有 enabled+task_class 相符 profile」這個需求的直接後果。序列化端由既有 `_MAX_PROVENANCE_ATTEMPTS`（6）截斷，不會無界膨脹。

新增的 provenance 記錄消耗 `elapsed_seconds=0.0` 且不遞增 `calls`，因此不影響 `len(attempts) >= self.max_attempts` 或 `calls >= session_call_budget` 的既有判斷時序——這些記錄不會讓迴圈多繞一圈。預算充足、迴圈正常走到最後一個 profile 而不觸發 break 的既有情境，程式路徑完全不經過補記程式碼，因此零回歸。

## Testing

- 新增：模擬鏈 `[claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]`，斷言 break 後 `attempts` 對應所有 enabled+task_class 相符 profile，被跳過者各有一筆 `failure_category="ineligible"` 記錄，且 raise 的 `category` / `profile_id` / `stderr` 取自 terminal 真實 attempt（park exactly as before 的回歸鎖）。
- 新增：生產事故形狀（claude timeout → codex timeout → deadline break），斷言 raise 報 `category="timeout"`、`profile_id="codex"`，不受 skip 尾端影響。
- 新增：attempt 中途 budget 耗盡（2-chunk session、chunk-0 吃光 deadline）→ 底部 break 同樣補記剩餘 profile（reason `session_budget`），raise 報 terminal 真實 attempt（`category="budget"`）。
- 新增：circuit-open 的剩餘 profile 在 deadline break 時不被補記。
- 新增：skip 補記可使 `len(attempts)` 超過 `max_attempts`（明文語意鎖）。
- 新增：預算充足時記錄行為與現行完全一致（回歸保護鎖）。
- 既有：`tests/test_external_agent_profiles.py` 全套綠；全套 pytest、`policy_check`、`openspec validate --all --strict` 綠。
