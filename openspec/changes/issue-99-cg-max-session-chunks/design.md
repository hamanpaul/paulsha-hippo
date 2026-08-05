---
status: accepted
work_item: issue-99-cg-max-session-chunks
---

# Issue #99 cg profile 大 session 停損（max_session_chunks）設計

- 日期：2026-08-04
- Issue：[#99](https://github.com/hamanpaul/paulsha-hippo/issues/99)
- 狀態：已核可，待實作

## 背景

cg profile 未宣告 `max_session_chunks`，導致超大 session 落到 cg 時燒滿 153 秒才以不可解析 JSON 失敗。`max_session_chunks` 欄位、`AgentProfile.eligible()` 的 `session_size` gate、以及對應的 provenance 記錄機制皆已由 #85/#100 為 tier-1（claude/codex）建立並在生產驗證（tier-1 上限 7）。證據與 root cause 詳見 spec（`docs/superpowers/specs/2026-08-04-issue-99-cg-max-session-chunks-spec.md`）。

## Decisions

### D1：cg 宣告 `max_session_chunks: 6`，機制沿用 #85/#100，不新增新機制

上限值依 issue 生產實證：6 chunks 內 cg 可解析輸出、7+ chunks 會踩到 exit 3「no parseable JSON」。沿用 `AgentProfile.eligible()` 既有的 `session_size` gate（`chunk_count > self.max_session_chunks` 時回傳 `(False, "session_size")`），不引入新 gate 型別、不新增新的 ineligible reason，與 tier-1 的 `_TIER1_MAX_SESSION_CHUNKS = 7` 同一套語意，僅數值與適用 profile（tier-2 cg）不同。

### D2：`atomizer.yaml` 與 `default_profiles()` 逐 token 同步

`paulsha_hippo/agent_profiles.py::default_profiles()` 的 cg row（`max_session_chunks` 欄位）與 `paulsha_hippo/atomizer/atomizer.yaml` 的 cg profile block（約 L72-82，新增 `max_session_chunks: 6`）必須同時修改。既有守門測試 `tests/test_external_agent_profiles.py::test_packaged_config_template_argv_matches_canonical_defaults` 驗證兩處逐 token 一致；本次變更後該測試必須維持綠燈。

### D3：根治留 issue #99 追蹤，明確不在本次範圍

copilot CLI 鏈路對大 payload 的 JSON 抽取／截斷行為，需要對真實 copilot CLI 打大 payload 才能量測其確切邊界，非確定性、不可自動化驗證，不適合以 TDD 單元測試鎖定。本次只做宣告式停損；根治留 issue #99 繼續追蹤，PR 交付語意用 `Refs #99`、不用 `Closes #99`。

## Testing

- 新增：chunk 數 7（超過 6）的 session，`AgentProfile.eligible(chunk_count=7)` 對 cg 回傳 `(False, "session_size")`，且不觸發實際呼叫（無 subprocess 執行副作用）。
- 新增：chunk 數 6（限內）的 session，cg 的 eligible 判定與呼叫路徑與變更前一致（零回歸）。
- 既有：`test_packaged_config_template_argv_matches_canonical_defaults` 綠——`atomizer.yaml` 與 `default_profiles()` 的 cg 定義逐 token 一致。
- 既有：全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
