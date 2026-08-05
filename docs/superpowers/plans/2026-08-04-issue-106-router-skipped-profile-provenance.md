---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Router deadline 提前 break 的 profile provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試再實作。**本檔案是全 repo dispatch 熱路徑，改動範圍必須最小。**

**Goal:** session 鏈預算被慢速 tier-1 吃掉、`RouterState` 主迴圈提前 `break` 時，被跳過的後段 profile 仍留下一筆 provenance 記錄（比照既有 `eligible=False` 記錄樣式），`attempts_detail` 不再「憑空少 profile」。

**Root cause 位置：** `paulsha_hippo/agent_profiles.py::RouterState` 主迴圈（約 L888-905）`time.monotonic() - started >= session_deadline` 判斷在進入下一個 profile 之前先 `break`，未記任何 ineligible/skipped 記錄。

**Target branch:** `feature/106-router-skip-provenance`

## Global Constraints

- 語言 zh-tw；TDD 先 RED 再實作。
- 交付治理：`changelog.d/<slug>.md`、pytest／policy_check／openspec strict 全綠、PR checklist 全勾、body `Closes #106`。
- **絕對最小 diff**：只補 provenance 記錄，**不得改變** dispatch 順序、deadline 算式、fallback 語意、park 行為。`FALLBACK_ON` allowlist 不可動。任何行為面（而非觀測面）的改變即為超範圍。
- 不得放寬 `FIXED_TIMEOUT_SECONDS`。
- 全套測試在非巢狀 sibling worktree 跑。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。

## Task 1: 提前 break 時補記 skipped provenance

**檔案**：`paulsha_hippo/agent_profiles.py`、`tests/test_external_agent_profiles.py`

- [ ] 先寫測試：stub executor 構造 profile 鏈 [claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]，斷言 break 後 attempts/provenance 含所有 enabled 且 task_class 匹配的 profile——被跳過者各有一筆記錄（建議 reason 沿用既有 ineligible 分類體系，如 `session_deadline`；不新增 fallback category）。
- [ ] 先寫測試：預算充足時記錄行為與現行完全一致（回歸保護）。
- [ ] 實作：break 前對剩餘 eligible profiles 逐一 append 一筆 skipped/ineligible 記錄，樣式比照緊鄰的 `eligible=False` pattern。
- [ ] 全套綠；`changelog.d/106-router-skip-provenance.md`（type: fix）。
