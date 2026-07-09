## ADDED Requirements

### Requirement: 記憶體 headroom 進入閘

`dream run --require-idle` MUST 在既有 load 閘之外，再檢查可用實體記憶體佔比：可用實體記憶體（`/proc/meminfo` 的 `MemAvailable`，本質不含 swap）除以總實體記憶體（`MemTotal`）MUST 嚴格大於 `--min-avail-mem-pct / 100` 才放行。未達門檻時 MUST 跳過該輪、以 exit code 0 結束，並輸出含 `skipped: "low memory"` 與 `backlog_depth` 的 JSON。記憶體判定 SHALL 為獨立述詞（例如 `lib.idle.has_mem_headroom`），MUST NOT 併入 `is_idle`（`is_idle` 維持只管 load）。當 `/proc/meminfo` 無法讀取或欄位缺失時，記憶體閘 MUST fail-safe 放行（等同只看 load）。

#### Scenario: 可用記憶體低於門檻時跳過
- **WHEN** `--require-idle` 生效、load 通過、但可用實體記憶體佔比 ≤ `--min-avail-mem-pct`
- **THEN** dream MUST 不執行 atomize/promote/janitor/moc，輸出含 `skipped: "low memory"` 的 JSON 並 exit 0

#### Scenario: 可用記憶體足夠時放行
- **WHEN** `--require-idle` 生效、load 通過、且可用實體記憶體佔比 > `--min-avail-mem-pct`
- **THEN** dream MUST 進入 atomize → promote → janitor → moc pipeline

#### Scenario: meminfo 讀不到時 fail-safe 放行
- **WHEN** 記憶體探測拋出例外或 `/proc/meminfo` 欄位缺失
- **THEN** 記憶體閘 MUST 回報放行（不因無法判定而卡住 dream）

### Requirement: `--min-avail-mem-pct` CLI 旗標

`hippo dream run` MUST 提供 `--min-avail-mem-pct` 旗標（浮點數，預設 `20.0`），語意為記憶體 headroom 閘的百分比門檻。CLI help MUST 與實際旗標同步（R-16）。

#### Scenario: 預設門檻為 20
- **WHEN** 呼叫 `hippo dream run --require-idle` 未指定 `--min-avail-mem-pct`
- **THEN** 記憶體閘 MUST 以 20% 為門檻

### Requirement: supervise 讓位 systemd timer

`hippo dream supervise` MUST 在進入前景 loop 之前偵測 systemd dream timer 是否已接管（`systemctl --user is-active paulsha-hippo-dream.timer` 回 `active`）。已接管時 MUST 讓位：不啟動前景 loop、以 exit code 0 結束並輸出讓位訊息。`systemctl` 不可用或 timer 非 active 時 MUST 視為未接管、照常執行前景 loop（no-systemd 主機語意不變）。讓位偵測 SHALL 可注入以利測試。

#### Scenario: timer active 時讓位
- **WHEN** `hippo dream supervise` 啟動且 dream systemd timer 為 active
- **THEN** MUST 不進入前景 loop、輸出讓位訊息並 exit 0

#### Scenario: timer 未接管時照跑
- **WHEN** `systemctl` 不可用或 dream timer 非 active
- **THEN** `hippo dream supervise` MUST 執行既有前景 loop

### Requirement: dream systemd 排程為每小時

dream systemd timer 模板 MUST 以 `OnCalendar=hourly` 排程並保留 `Persistent=true`（補跑錯過的排程）。

#### Scenario: timer 模板為 hourly
- **WHEN** 讀取 dream timer 模板
- **THEN** MUST 含 `OnCalendar=hourly` 且 MUST NOT 含 `Mon..Fri` 排程

### Requirement: dream systemd 資源上限（可攜）

dream systemd service 模板的 `[Service]` MUST 宣告可攜（實體 RAM 百分比）cgroup 資源治理：`CPUWeight=20`、`MemoryHigh=20%`、`MemoryMax=30%`、`TasksMax=256`，且 MUST NOT 宣告 `CPUQuota`。`install service` 既有的單元改名與 `ExecStart` 綁定安裝環境 interpreter 之行為 MUST 不受影響。

#### Scenario: service 模板含資源上限、無 CPUQuota
- **WHEN** 讀取 dream service 模板
- **THEN** MUST 含 `CPUWeight`、`MemoryHigh`、`MemoryMax`、`TasksMax`，且 MUST NOT 含 `CPUQuota`
