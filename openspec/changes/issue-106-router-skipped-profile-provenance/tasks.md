---
status: accepted
work_item: issue-106-router-skipped-profile-provenance
---

# Issue #106 Router deadline 提前 break 的 profile provenance tasks

- [x] 1.1 先寫測試：stub executor 構造 profile 鏈 [claude(慢、吃光大半 session_deadline), codex, cg, local-vllm]，斷言 break 後 attempts/provenance 含所有 enabled 且 task_class 匹配的 profile——被跳過者各有一筆記錄（建議 reason 沿用既有 ineligible 分類體系，如 `session_deadline`；不新增 fallback category）。
- [x] 1.2 先寫測試：預算充足時記錄行為與現行完全一致（回歸保護）。
- [x] 1.3 實作：break 前對剩餘 eligible profiles 逐一 append 一筆 skipped/ineligible 記錄，樣式比照緊鄰的 `eligible=False` pattern。
- [x] 1.4 全套綠；`changelog.d/106-router-skip-provenance.md`（type: fix）。
- [x] 2.1 Review fix（BLOCKING）：`_raise_exhausted` 錨定 terminal 真實 attempt——`run_session` 全程追蹤最後一筆真實記錄並顯式傳入，raise 的 category/profile_id/exit_code/stderr 不再被合成 skip 尾端改寫；補 raise 內容回歸鎖測試（deadline break 斷言 `category=="timeout"`、`profile_id` 為真實嘗試過的 profile）。
- [x] 2.2 Review fix（MAJOR）：skip 補記延伸到 attempt 中途 budget 耗盡的底部 break 路徑（reason `session_budget`），多 chunk session 的剩餘 profile 不再零記錄消失；補 2-chunk 測試。
- [x] 2.3 Review fix（MINOR）：skip 補記檢查 `_circuit_open_until`，circuit-open 剩餘 profile 維持無聲跳過語意；補測試與 design D1 決策。
- [x] 2.4 Review fix（MINOR）：明文記載 skip 補記可使 `len(attempts)` 超過 `max_attempts`、park attempts 計數含未執行 profile（design D3 + openspec requirement），補語意鎖測試。
