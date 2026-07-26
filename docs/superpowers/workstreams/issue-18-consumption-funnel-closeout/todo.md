# Issue #18 consumption funnel closeout / todo

## 交付契約（已完成）

- [x] session citation 契約：同 session 的 `offered/read` 斷點可追溯（含 session_id）
- [x] distinct unique-slice coverage 契約：slice 追蹤與 coverage 去重不混淆
- [x] offered-to-read conversion 契約：有 offered → read 實際轉換證據
- [x] `applied` 分開計算：與 citation/read 明確區隔
- [x] wheel-installed entrypoint 契約：以 wheel installed entrypoint 驗證 delivery path
- [x] 測試 / policy / OpenSpec 契約：候選與回歸檢查已完成
- [x] Cortex archive/merge 前置契約：已對齊 PR #60 並備妥交付資料

## 關聯文件

- [Issue #18](https://github.com/hamanpaul/paulsha-hippo/issues/18)
- [PR #60](https://github.com/hamanpaul/paulsha-hippo/pull/60)
- [既有 Plan：2026-07-26-issue-18-consumption-funnel-closeout](../../plans/2026-07-26-issue-18-consumption-funnel-closeout.md)
- [OpenSpec：issue-18-consumption-funnel-closeout](../../../../openspec/changes/archive/2026-07-26-issue-18-consumption-funnel-closeout/tasks.md)
