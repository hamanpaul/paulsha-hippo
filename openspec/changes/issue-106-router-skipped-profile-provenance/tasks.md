---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Issue #106 Router deadline 提前 break 的 profile provenance tasks

- [x] 1.1 先寫測試：stub executor 構造 profile 鏈 [claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]，斷言 break 後 attempts/provenance 含所有 enabled 且 task_class 匹配的 profile——被跳過者各有一筆記錄（建議 reason 沿用既有 ineligible 分類體系，如 `session_deadline`；不新增 fallback category）。
- [x] 1.2 先寫測試：預算充足時記錄行為與現行完全一致（回歸保護）。
- [ ] 1.3 實作：break 前對剩餘 eligible profiles 逐一 append 一筆 skipped/ineligible 記錄，樣式比照緊鄰的 `eligible=False` pattern。
- [ ] 1.4 全套綠；`changelog.d/106-router-skip-provenance.md`（type: fix）。
