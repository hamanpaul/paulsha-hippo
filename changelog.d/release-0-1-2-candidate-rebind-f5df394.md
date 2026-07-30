---
type: chore
---
### Changed

- 0.1.2 candidate 自 `2f3dc98` 重綁至 `f5df394`（維護者裁定，2026-07-30）：舊綁定因 #85／#88／#90／#91／#92／#93／#94 等八個 merge 漂移而失效，依 `readiness.bind_candidate` 漂移語意作廢舊 AR-01 證據並對新 candidate 重跑（CI Tests success：1700 passed／4 skipped／158 subtests；本機非巢狀 sibling worktree 實跑數字一致）。AR-11 evidence 同步改寫為 PR #91 的窗口語意：記錄前一部署 build `76edb93` 於 2026-07-30 完成的 3/3 soak（03:00Z／10:00Z／11:00Z 三合格輪，ledger 為證）屬該 build 之 attest 不可轉移；本 candidate 於 11:24Z 部署後依換版規則開新窗口，soak 0/3 重新累積。
