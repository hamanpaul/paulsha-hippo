# dream 資源治理設計（idle+mem 雙閘 / supervise 讓位 / systemd 資源上限）

- 日期：2026-07-09
- repo：paulsha-hippo（記憶平面）
- 分支：`feature/dream-resource-governance`
- 狀態：設計已收斂，待實作（TDD）
- profile：flat；tier：shareable（本文與程式碼皆須通過 R-21 機密掃描——不得含個人絕對路徑／機敏標記）

## 背景與問題

dream（atomize → promote → janitor → moc 一輪）目前**沒有資源治理**，具體三個缺口：

1. **`--require-idle` 只看 CPU load**（`lib/idle.py::is_idle` 讀 1 分鐘 loadavg ≤ `--max-load`）。記憶體吃緊時仍會放行；distiller backend 為 `claude-headless`（本機會起 Node `claude` 子程序），低記憶體下起跑易踩 OOM。
2. **雙驅動可能雙跑**：systemd `paulsha-hippo-dream.timer`（排程 `dream run`）與 operator shell 呼叫的 `hippo dream supervise`（前景每 interval 跑一次）彼此不知情；兩者同時啟用會對同一 memory DB 並發，產生毒快取/race 風險。
3. **執行期無上限**：dream service unit 是裸 `Type=oneshot`，無任何 CPU/記憶體 cgroup 限制；一旦起跑可無限佔用資源。

## 目標

- G1：`--require-idle` 增加**記憶體 headroom 閘**——可用實體記憶體（不含 swap）需 > 門檻百分比才放行。
- G2：`hippo dream supervise` 偵測到 systemd dream timer 已接管時**自動讓位**，避免雙跑（且**不需**改動 operator shell / 不需 env flag）。
- G3：systemd 模板改為 **hourly** 排程，並加**可攜（百分比）資源治理**：低 CPU 優先權 + 記憶體軟/硬上限 + tasks 上限。

## 非目標

- 不改 load 門檻預設（`--max-load` 維持 1.0）。
- 不動 distill / atomize / janitor / moc 任何邏輯。
- 不在 consumer（operator shell 的 `start.sh`）打補丁——資源政策屬記憶平面自身職責，一律收斂於 hippo。

## 設計

### G1：記憶體 headroom 閘

**`paulsha_hippo/lib/idle.py`**（維持「自足件 + probe 可注入 + fail-safe」風格，與 `is_idle` 對稱）新增：

```
def has_mem_headroom(min_fraction: float = 0.20, probe=_read_meminfo) -> bool:
    """可用實體記憶體 / 總實體記憶體 > min_fraction 時回 True。
    採 /proc/meminfo 的 MemAvailable / MemTotal——MemAvailable 為可用實體 RAM，
    本質不計 swap（即需求的「不含 swp」）。讀不到時 fail-safe 回 True（寧跑不卡）。"""
```

- `_read_meminfo()`：解析 `/proc/meminfo` 為 dict（`MemTotal` / `MemAvailable`，單位 kB）；probe 可注入供測試。
- 邊界：`MemTotal <= 0` 或欄位缺失／解析失敗 → 回 True（fail-safe）。
- 判準為 `avail / total > min_fraction`（嚴格大於），與需求「大於 20% 才放行」一致。

**`paulsha_hippo/dream/cli.py::_run`** 閘擴為兩道、skip 原因分開（觀測性）：

```
if args.require_idle and not idle.is_idle(max_load=args.max_load):
    -> skip {"skipped": "system busy", "backlog_depth": N}; return 0
if args.require_idle and not idle.has_mem_headroom(args.min_avail_mem_pct / 100.0):
    -> skip {"skipped": "low memory", "avail_pct": <float>, "backlog_depth": N}; return 0
```

**`paulsha_hippo/cli.py`**：`dream run` 新增 `--min-avail-mem-pct`（`type=float`，預設 `20.0`），與既有 `--max-load` 對稱。

### G2：supervise 讓位 systemd

**`paulsha_hippo/ops.py::run_dream_supervise`**：進入 loop 前先偵測 systemd dream timer 是否接管；接管即讓位：

```
def run_dream_supervise(*, interval, extra_argv=None, once=False, runner=None,
                        timer_active=_dream_timer_active) -> int:
    if timer_active():
        print("systemd dream timer 已接管；supervise 讓位（不啟動前景 loop）")
        return 0
    ...  # 既有 loop 不變
```

- `_dream_timer_active()`：`systemctl --user is-active paulsha-hippo-dream.timer` 之 stdout == `"active"`（與 `run_doctor` 既有檢查同一招）。`systemctl` 不存在／非 active → 回 False（視為未接管，supervise 照跑；no-systemd 主機語意不變）。
- `timer_active` 參數化以利測試注入。

### G3：systemd 模板（hourly + 可攜資源治理）

**`paulsha_hippo/dream/systemd/paulsha-memory-dream.timer`**

```ini
[Timer]
OnCalendar=hourly
Persistent=true
```

**`paulsha_hippo/dream/systemd/paulsha-memory-dream.service`** `[Service]` 追加：

```ini
CPUWeight=20        # 低優先：忙時讓位互動工作、閒時可用滿閒置核心（相對值，換機自動縮放）
MemoryHigh=20%      # 軟上限：超過即節流+回收（實體 RAM 百分比）
MemoryMax=30%       # 硬上限 backstop：僅暴衝時才 kill（留 claude headroom）
TasksMax=256        # 防 fork 暴走
```

- **不設 `CPUQuota`**：dream distill 為序列單一子程序、且僅在 idle 放行時起跑，硬砍核數反而拉長佔用；改用 `CPUWeight` 低優先（相對、可攜）。
- 記憶體採百分比 → 不寫死機器專屬數字（滿足 tier:shareable 與跨機可攜）。
- `install service`（`ops.py::run_install_service`）既有的 `paulsha-memory-dream → paulsha-hippo-dream` 改名與 `ExecStart` 綁定 `sys.executable` 邏輯不變，模板變更自動流經。

## 資料流

```
systemd timer (hourly, Persistent)
  └─> dream run --require-idle --min-avail-mem-pct 20 --promoter llm
        ├─ gate1 load  ≤ max_load?      否 -> skip "system busy"
        ├─ gate2 mem   avail% > 20%?     否 -> skip "low memory"
        └─ 皆通過 -> atomize → promote → janitor → moc
                     （執行期受 CPUWeight/MemoryHigh/MemoryMax/TasksMax 治理）

operator shell start.sh -> hippo dream supervise
  └─ timer active? 是 -> 讓位 return 0（不雙跑）
                    否 -> 既有前景 loop（no-systemd 主機）
```

## 錯誤處理與相容

- `/proc/meminfo` 讀不到（非 Linux／權限）→ `has_mem_headroom` fail-safe 放行，行為回退為「只看 load」。
- `systemctl` 不可用 → supervise 視為未接管、照跑，維持 no-systemd 主機既有語意。
- `is_idle` 維持單一職責（只管 load），mem 閘為獨立述詞，兩者在 `dream/cli.py` 組合。

## 測試（TDD，先寫測試）

- `tests/test_dream_idle.py`：`has_mem_headroom` 高於／低於／等於門檻（邊界）、probe fail-safe（拋例外回 True）、`_read_meminfo` 解析。
- `tests/test_dream_cli.py`：`--require-idle` 記憶體不足 → skip `"low memory"`（注入 probe）；記憶體足 + load 足 → 進入 pipeline。
- `tests/test_ops.py`：`run_dream_supervise` 於 `timer_active=lambda: True` → return 0 不進 loop；`False` → 進 loop（以 `once=True` + 假 runner 驗證）。
- `tests/test_dream_systemd_template.py`：timer `OnCalendar=hourly`（**更新既有 `test_timer_has_workday_morning_schedule` 的 `Mon..Fri` 斷言**）；service 含 `CPUWeight`/`MemoryHigh`/`MemoryMax`/`TasksMax`、不含 `CPUQuota`。

## 政策/交付 checklist（hippo，policy v1.0.12）

- 分支 `feature/dream-resource-governance`（非 main、slug 無小數點）。
- `CHANGELOG.md [Unreleased]` 補 entry（R-09；本為 code change）。
- 新增 CLI 旗標 → 同步 R-16 CLI help（若有 help 快照測試一併更新）。
- PR title 走 conventional-commit（R-10）；body checklist 全勾（R-11）；zh-tw。
- `python3 -m policy_check --repo .` 零 failure；tier:shareable → 內容不得含個人絕對路徑／機敏標記（R-21）。

## Rollout（整合驗證）

1. hippo PR → review → merge。
2. bump 主 repo `paulshaclaw/pyproject.toml` 的 `paulsha-hippo` pin 至含本 PR 的新 SHA。
3. 重裝（`pip install -e .` + `pipx install ... --force`）→ `hippo install service --enable`（hourly timer 上線）。
4. 驗證：手動 `hippo dream run --require-idle --min-avail-mem-pct 20`（觀察 skip 分支）、`systemctl --user list-timers` 見 hourly、`hippo dream supervise --once` 在 timer active 下即讓位、`systemctl --user show paulsha-hippo-dream.service -p CPUWeight -p MemoryHigh -p MemoryMax -p TasksMax` 確認 caps 生效。

## 風險

- **改 shipped cadence**（Mon..Fri 05:00 → hourly）影響所有 hippo 安裝（實務上單一使用者），已確認；hourly 配 idle+mem 雙閘實際只在機器閒時真跑。
- `MemoryMax=30%` 硬上限於 distill 暴衝時仍可能 OOM 該輪，但配「> 20% free 才起跑」的入口閘，風險已壓低；後續可視實測再調。
