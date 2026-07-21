### Added
- `hippo dream reconcile --dry-run/--apply`：`inbox/_slices` fragment 與 processing ledger 對賬工具。dry-run 產出分類報告（orphan-fragment / terminal-unarchived / stale-split / healthy），apply 逐項修復——orphan 補建 split state、terminal archive fragment、stale 標記 no-findings。全程持 dream lock、寫 dream ledger、支持 `--limit N` 分批（#36）。
- `hippo doctor` timer 健檢擴充：新增 `LastTriggerUSec`/`NextElapseUSecRealtime`/`UnitFileState` 有效性檢查（n/a 或 stale → WARN）+ timer unit drift 偵測（部署 timer 與 repo template `OnCalendar`/`Persistent` 等欄位差異 → WARN，只報告不覆寫）（#36）。
- `hippo install service --enable` 後追加 `is-active` + `is-enabled` 驗證，失敗 return 1 + 診斷；成功印 baseline `LastTriggerUSec`（#36）。
