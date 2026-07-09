## Why

dream（記憶蒸餾常駐）目前無資源治理：`--require-idle` 只看 CPU load、不看記憶體，低記憶體下起跑會踩 `claude-headless` distiller 的 OOM；systemd timer 與 operator shell 的 `hippo dream supervise` 互不知情、可能雙跑並發同一 memory DB；dream service unit 無任何 CPU/記憶體 cgroup 上限，起跑後可無限佔用資源。資源政策應收斂於記憶平面自身，而非在 consumer（operator shell）打補丁。

## What Changes

- `hippo dream run --require-idle` 增加**記憶體 headroom 閘**：可用實體記憶體（不含 swap）需 `> --min-avail-mem-pct`（預設 20%）才放行，否則跳過該輪並記 `skipped: "low memory"`。
- 新增 CLI 旗標 `dream run --min-avail-mem-pct`（float，預設 20.0），與既有 `--max-load` 對稱。
- `hippo dream supervise` 偵測 systemd dream timer 已 active 時**讓位**（return 0、不啟動前景 loop），避免與 timer 雙跑。
- **BREAKING（行為變更）**：dream systemd timer 排程由 `Mon..Fri 05:00` 改為 `OnCalendar=hourly`。
- dream systemd service 加入可攜（實體 RAM 百分比）cgroup 資源治理：`CPUWeight=20`、`MemoryHigh=20%`、`MemoryMax=30%`、`TasksMax=256`（不設 `CPUQuota`）。

## Capabilities

### New Capabilities
- `dream-resource-governance`: dream 常駐的資源進入閘（load + 記憶體雙閘）、與 systemd timer 的單一驅動仲裁（supervise 讓位）、以及 dream systemd 單元的排程與 cgroup 資源上限契約。

### Modified Capabilities
- `stage2-memory-governance`: dream 排程 requirement 由 Mon–Fri morning 改為交由 dream-resource-governance 治理（hourly）。

## Impact

- 程式：`paulsha_hippo/lib/idle.py`（新增記憶體 headroom 述詞）、`paulsha_hippo/dream/cli.py`（第二道閘）、`paulsha_hippo/cli.py`（新 CLI 旗標）、`paulsha_hippo/ops.py`（supervise 讓位）、`paulsha_hippo/dream/systemd/paulsha-memory-dream.{service,timer}`（hourly + cgroup 上限）。
- 測試：`tests/test_dream_idle.py`、`tests/test_dream_cli.py`、`tests/test_ops.py`、`tests/test_dream_systemd_template.py`（更新既有 `Mon..Fri` 斷言）。
- 消費端：主 repo `paulshaclaw` bump `paulsha-hippo` pin 後，`hippo install service --enable` 即帶入 hourly + 資源上限；operator shell `start.sh` 的 `hippo dream supervise` 因新讓位邏輯自動不雙跑，**無需改動 start.sh**。
- 相容：`/proc/meminfo` 讀不到或 `systemctl` 不可用時 fail-safe（放行 / 視為未接管），維持 no-systemd 主機既有語意。
