---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# Issue #80 大 session chunk 序列預算與部分成果保留 tasks

依 TDD：每個實作任務前先落紅燈測試，看到預期的失敗原因再實作。測試置於 `tests/test_external_agent_profiles.py`（既有）與相關 atomizer 測試檔。

實作細節與紅線見 `docs/superpowers/plans/2026-07-28-issue-80-atomize-chunk-budget.md`；三個 Task 依風險遞增排序，**Task 3 做不完不影響 Task 1、2 已交付的價值**。

## 1. session 預算隨 chunk 數縮放

- [ ] 1.1 紅燈：有效預算對 1、2、7、100 個 chunk 分別為 600、600、1680、1800 秒
- [ ] 1.2 紅燈：單次呼叫拿到的 timeout 仍為 `min(profile.timeout, remaining_seconds)` 且不超過 `FIXED_TIMEOUT_SECONDS = 300`
- [ ] 1.3 綠燈：新增 `FIXED_PER_CHUNK_DEADLINE_SECONDS = 240` 與 `FIXED_SESSION_DEADLINE_CAP_SECONDS = 1800`，`run_session` 改用 `min(CAP, max(FLOOR, PER_CHUNK × chunk 數))`
- [ ] 1.4 常數處補註解說明三個數字的實測來源

## 2. profile 宣告 `max_session_chunks`

- [ ] 2.1 紅燈：`from_mapping` 接受正整數、拒絕 0／負數／非整數（`ProfileConfigError`）、缺省為 `None`
- [ ] 2.2 紅燈：`eligible(chunk_count=7)` 在 `max_session_chunks=6` 時回 `(False, "session_size")`；`chunk_count=6` 或 `None` 時 eligible
- [ ] 2.3 紅燈：router 跳過超出宣告的 profile 時，attempt 進 provenance（`failure_category="ineligible"`）且 agent call 計數不增加
- [ ] 2.4 紅燈：canonical default 的 `claude`／`codex` 為 6、`cg` 為 `None`，且 `atomizer.yaml` 與 canonical default 一致
- [ ] 2.5 綠燈：欄位、驗證、`eligible()` 的 keyword-only `chunk_count`、router 傳入 `len(frozen_prompts)`
- [ ] 2.6 綠燈：`cache_namespace()` 納入 `max_session_chunks`；**不**納入 `command_fingerprint`／`cache_identity`

## 3. 已驗證 chunk 跨 profile 保留與續跑

- [ ] 3.1 紅燈：profile A 完成 chunk 0、1 後於 chunk 2 失敗，profile B 只收到 chunk 2，最終 outputs 為 `[A0, A1, B2]`
- [ ] 3.2 紅燈：provenance 記得出每個 chunk 的 profile，session 層 `fallback_reason` 為 `degraded-success`
- [ ] 3.3 紅燈：`CachingAgentClient` 於 chunk 驗證通過當下即落地該 chunk，且不同 profile 的 chunk 不互相污染
- [ ] 3.4 紅燈（回歸）：全數耗盡時仍拋 `AgentRunError`、仍 park、`attempts_detail` 的 `failure_kind` 分類不變、無 partial publication
- [ ] 3.5 綠燈：`run_session` 改為「已驗證前綴 + 續跑起點」，per-chunk provenance 傳遞至 `agent_exec` 與 `llm_promoter`

## 4. 交付治理

- [ ] 4.1 `changelog.d/80-atomize-chunk-budget.md`（zh-tw，誠實反映實際完成的 Task 範圍）
- [ ] 4.2 `rm -rf .psc_tmp`，逐檔 `git add`（不得 `git add -A`）
- [ ] 4.3 非巢狀 worktree 跑 `python3 -m pytest -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 三者皆綠
- [ ] 4.4 PR body 用 `.github/pull_request_template.md`、checklist 全勾、附實際測試輸出數字、以 closing keyword 關閉 issue #80
