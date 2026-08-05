---
status: accepted
work_item: issue-99-cg-max-session-chunks
---

# cg profile 大 session 停損（max_session_chunks）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試再實作。

**Goal:** 大 session（>6 chunks）不再讓 cg 燒 153 秒才輸出不可解析 JSON 失敗——cg profile 宣告既有欄位 `max_session_chunks`，超限即記 `ineligible` provenance、立即讓給下一個 profile。

**範圍限定（停損版）：** 本 plan 只做宣告式停損。「根治」（copilot 鏈路 JSON 抽取對大 payload 的截斷行為）需要對真實 copilot CLI 打大 payload 量測，非確定性可自動化，**明確不在本 plan 範圍**，留在 issue #99 追蹤。

**機制現況：** `max_session_chunks` 欄位與 ineligible 路徑已存在（#85/#100 為 claude/codex tier-1 建立，上限 7）。本次只是讓 cg profile 也宣告它。

**Target branch:** `feature/99-cg-max-session-chunks`

## Global Constraints

- 語言 zh-tw；TDD 先 RED 再實作。
- 交付治理：`changelog.d/<slug>.md`、pytest／policy_check／openspec strict 全綠、PR checklist 全勾、body 註 `Refs #99`（**不要用 Closes**——issue 的根治部分未完成，比照 PR #56 慣例附 `policy-exempt:issue-link` label 或在 body 說明只交付停損）。
- **出貨模板漂移守門**：`paulsha_hippo/atomizer/atomizer.yaml` 的 profile 定義必須與 `agent_profiles.default_profiles()` 逐 token 同步（`tests/test_external_agent_profiles.py::test_packaged_config_template_argv_matches_canonical_defaults`），兩處一起改。
- 全套測試在非巢狀 sibling worktree 跑。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。

## Task 1: cg 宣告 max_session_chunks

**檔案**：`paulsha_hippo/agent_profiles.py`（`default_profiles()` 的 cg 定義）、`paulsha_hippo/atomizer/atomizer.yaml`（cg profile block，約 L72-82）、`tests/test_external_agent_profiles.py`

- [ ] 先寫測試：比照既有 tier-1 `max_session_chunks` 測試模式，構造 chunk 數超限的 session，斷言 cg 被記 `ineligible`（provenance record 存在、reason 正確）而非實際嘗試呼叫。
- [ ] 先寫測試：chunk 數在限內時 cg 行為不變。
- [ ] 實作：cg profile 加 `max_session_chunks: 6`（依 issue 實證：6 chunks 內可解析、7+ 不可）；`atomizer.yaml` 與 `default_profiles()` 兩處同步。
- [ ] 模板同步測試綠；全套綠；`changelog.d/99-cg-max-session-chunks.md`（type: fix）。
