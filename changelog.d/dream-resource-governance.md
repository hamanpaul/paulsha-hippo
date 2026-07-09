### Added
- dream `--require-idle` 增加記憶體 headroom 閘，新增 `--min-avail-mem-pct`（預設 20%）並在低記憶體時輸出 `skipped: "low memory"` 與 `avail_pct`。
- `hippo dream supervise` 偵測 systemd dream timer 已接管時會讓位，避免與 timer 雙跑。

### Changed
- dream systemd timer 排程改為 `OnCalendar=hourly`，並保留 `Persistent=true`。
- dream systemd service 新增 `CPUWeight=20`、`MemoryHigh=20%`、`MemoryMax=30%`、`TasksMax=256` 的可攜 cgroup 資源上限，且不設 `CPUQuota`。
