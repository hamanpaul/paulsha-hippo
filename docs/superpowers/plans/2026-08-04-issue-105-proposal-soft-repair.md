---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Proposal 未知欄位 soft-repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試再實作。**本檔案守著一條明文設計不變量，修法必須顯式處理它。**

**Goal:** LLM 回應中單一 proposal 帶 schema 不認得的「未知欄位」（如 `tags2`）時，不再讓整份回應（其他 N 個合法 proposal）一起判死——未知欄位屬可安全丟棄的 soft violation，丟棄該欄位（或僅剔除該 proposal）並記 warning；真正的 hard violation 維持整份判死。

**Root cause 位置：** `paulsha_hippo/atomizer/llm_output.py::_build_proposal`（約 L211-267）把 unknown fields 併入 hard-field violation，`_parse_proposals`（L270-283）因而 raise `LlmOutputError` 使整份失效，fallback 鏈全滅 → session park。

**設計不變量（程式碼內明文註解）：** 「一個 proposal 拖垮整份回應」是刻意設計——防止 retry 非決定性遺失 findings。**修法必須二選一：更新該註解並論證縮小失效範圍為何不違反其目的（未知欄位丟棄是決定性操作、不產生 retry），或證明修法沒動到該不變量。不得默默繞過。**

**Target branch:** `feature/105-proposal-soft-repair`

## Global Constraints

- 語言 zh-tw；TDD 先 RED 再實作。
- 交付治理：`changelog.d/<slug>.md`、pytest／policy_check／openspec strict 全綠、PR checklist 全勾、body `Closes #105`。
- 這是每輪 dream cycle 必經路徑：**hard violation 的判死語意一條都不能鬆**——缺 title、非法 artifact_kind、型別錯誤等仍須整份 `LlmOutputError`。
- 全套測試在非巢狀 sibling worktree 跑。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。

## Task 1: 未知欄位降級為 soft violation

**檔案**：`paulsha_hippo/atomizer/llm_output.py`、`tests/test_llm_output.py`（或既有對應測試檔）

- [ ] 先寫測試：N 個合法 proposal + 1 個帶未知欄位 `tags2` 的回應——修復後不拋 `LlmOutputError`，未知欄位被丟棄（或該 proposal 被剔除並記 warning），其餘 proposal 正常產出。
- [ ] 先寫測試（不變量保護）：缺 `title`／非法 `artifact_kind` 等 hard violation 仍讓**整份**回應判死——逐一列舉既有 hard 條件為測試案例。
- [ ] 先寫測試：warning 內容含被丟棄的欄位名與 proposal 序號（可觀測）。
- [ ] 實作：`_build_proposal` 把 unknown-field 從 hard 清單分離為 soft 處理；更新該設計註解說明分界理由。
- [ ] 全套綠；`changelog.d/105-proposal-soft-repair.md`（type: fix）。
