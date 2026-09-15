---
type: chore
---
- `.cortex/work-items.yaml` 補登錄 12 個已交付 cortex work item（issue #18／#34／#41／#64／#74／#80／#98／#105／#106／#109／#136 對應的規劃 workstream，以及 `issue-18-luna-closeout-followup-v7` 的 closeout 票 #153）的 `github_issue` link；這批 issue 皆已 CLOSED、PR 皆已 merged，只剩 `docs/superpowers/` 規劃文件殘留，使 cortex daemon 每 tick 判 `missing_issue` 並在 `cortex status` 的 `not_claimable` 反覆觀測（cortex#669 表象、cortex#895 治標）。綁定後 daemon 走 `ignore: auto-label-missing`，不建 run、不派工。
