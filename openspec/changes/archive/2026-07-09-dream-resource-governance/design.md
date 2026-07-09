## Context

dream 常駐（atomize → promote → janitor → moc 一輪）由兩條路徑驅動：systemd `paulsha-hippo-dream.timer`（排程 `dream run`）與 operator shell 呼叫的 `hippo dream supervise`（前景每 interval 一輪）。目前 `--require-idle` 僅檢查 1 分鐘 loadavg（`lib/idle.py::is_idle`），不檢查記憶體；distiller backend 為 `claude-headless`（本機起 Node 子程序），低記憶體下起跑易 OOM。兩條驅動彼此不知情，同時啟用會並發同一 memory DB。dream service unit 為裸 `Type=oneshot`，無 cgroup 上限。完整設計脈絡見 `docs/superpowers/specs/2026-07-09-dream-resource-governance-design.md`。

## Goals / Non-Goals

**Goals:**
- `--require-idle` 增加記憶體 headroom 閘（可用實體記憶體百分比門檻，不含 swap）。
- `hippo dream supervise` 於 systemd timer 接管時讓位，避免雙跑（不改動 operator shell）。
- dream systemd 模板改 hourly 排程 + 可攜（百分比）cgroup 資源上限。

**Non-Goals:**
- 不改 load 門檻預設（`--max-load` 維持 1.0）。
- 不動 atomize / promote / janitor / moc 邏輯。
- 不在 consumer（`paulshaclaw/start.sh`）打補丁——資源政策收斂於記憶平面。

## Decisions

- **記憶體指標採 `/proc/meminfo` 的 `MemAvailable/MemTotal`**（而非 `MemFree`）：MemAvailable 是核心對「可用實體 RAM」的估計、本質不計 swap，正合「不含 swp」需求；MemFree 過嚴（不含可回收 cache）。
- **門檻以百分比表達（預設 20%）而非絕對 GiB**：跨機自動比例縮放，且符合 tier:shareable（不寫死機器專屬數字）。
- **記憶體閘為獨立述詞 `has_mem_headroom`，不塞進 `is_idle`**：`is_idle` 維持單一職責（只管 load），兩閘在 `dream/cli.py` 組合，skip 原因分開記（`system busy` / `low memory`）以利觀測。
- **CPU 治理用 `CPUWeight` 而非 `CPUQuota`**：dream distill 為序列單一子程序且僅在 idle 放行時起跑，硬砍核數反拖長佔用；`CPUWeight=20`（低於預設 100）閒時可用滿閒置核心、忙時自動讓位，且為相對值可攜。替代方案 `CPUQuota=200%`（硬 2 核）被否決——固定核數不隨機器縮放、且浪費閒置資源。
- **supervise 讓位偵測用 `systemctl --user is-active paulsha-hippo-dream.timer`**：與 `run_doctor` 既有檢查同一招，一致且零新相依；`systemctl` 不可用→視為未接管，supervise 照跑（no-systemd 主機語意不變）。
- **fail-safe 一律放行**：`/proc/meminfo` 讀不到→`has_mem_headroom` 回 True（退化為只看 load），寧跑不卡。

## Risks / Trade-offs

- [改 shipped cadence（Mon..Fri 05:00 → hourly）影響所有 hippo 安裝] → 實務上單一使用者；hourly 配 idle+mem 雙閘實際只在機器閒時真跑，且已與使用者確認。
- [`MemoryMax=30%` 硬上限於 distill 暴衝時仍可能 OOM 該輪] → 配「> 20% free 才起跑」的入口閘壓低機率；百分比留 headroom（軟 20% / 硬 30%），後續可依實測調整。
- [百分比 cgroup 值需 `MemoryAccounting` 生效] → systemd user manager 預設 `DefaultMemoryAccounting=yes`；controller 未委派時上限不生效但不致命（退化為無上限，行為同今日）。

## Migration Plan

1. hippo PR merge。
2. 主 repo `paulshaclaw/pyproject.toml` bump `paulsha-hippo` pin 至含本變更之 SHA。
3. 重裝（`pip install -e .` + `pipx install ... --force`）→ `hippo install service --enable`（hourly timer 上線、cgroup 上限隨模板生效）。
4. 回滾：還原 pin 至前一 SHA 重裝即可；runtime 狀態零遷移。
