---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Proposal 未知欄位 soft-repair tasks

依 TDD：每個實作任務前先落紅燈測試。測試置於 `tests/test_llm_output.py`。

## Task 1: 未知欄位降級為 soft violation

- [x] 先寫測試：N 個合法 proposal + 1 個帶未知欄位 `tags2` 的回應——修復後不拋 `LlmOutputError`，未知欄位被丟棄（或該 proposal 被剔除並記 warning），其餘 proposal 正常產出。
- [x] 先寫測試（不變量保護）：缺 `title`／非法 `artifact_kind` 等 hard violation 仍讓**整份**回應判死——逐一列舉既有 hard 條件為測試案例。
- [x] 先寫測試：warning 內容含被丟棄的欄位名與 proposal 序號（可觀測）。
- [ ] 實作：`_build_proposal` 把 unknown-field 從 hard 清單分離為 soft 處理；更新該設計註解說明分界理由。
- [ ] 全套綠；`changelog.d/105-proposal-soft-repair.md`（type: fix）。
