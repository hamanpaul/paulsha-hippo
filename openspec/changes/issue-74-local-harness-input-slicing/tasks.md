---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# local-harness map-reduce 輸入切分 Tasks

依 TDD：先寫失敗測試再實作。測試置於 `contrib/local-harness/tests/test_harness_slicing.py`。

## Task 1: 純函式輸入裁切

- [x] 1.1 先寫測試：新純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`——合成含 8 個 `[fragment N]` 區塊的 prompt，`indices={3}` 時輸出僅含 fragment 2/3/4 與 preamble、`## Output` 區塊；bytes 顯著小於原文。
- [x] 1.2 先寫測試：indices 越界／空集合／單 fragment prompt 的邊界行為（fail-safe：裁切失敗時退回全量並記 warning，不得讓 write 直接失敗）。
- [ ] 1.3 實作純函式 `slice_prompt_by_fragments` 並接進 pass 2 write 呼叫點。
- [ ] 1.4 全套綠；`changelog.d/74-harness-input-slicing.md`（type: fix）。
