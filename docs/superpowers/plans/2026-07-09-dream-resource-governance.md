# dream 資源治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 hippo dream 具備資源治理——`--require-idle` 加記憶體 headroom 閘、supervise 讓位 systemd timer、dream systemd 模板改 hourly + 可攜 cgroup 上限。

**Architecture:** 全部收斂於 `paulsha-hippo`（記憶平面），不動 consumer（`paulshaclaw/start.sh`）。記憶體閘為 `lib/idle.py` 獨立述詞、在 `dream/cli.py` 與 load 閘組合；supervise 讓位邏輯內建於 `ops.py::run_dream_supervise`（預設 probe 查 systemd）；排程與 cgroup 上限寫進 `dream/systemd/*` 模板。

**Tech Stack:** Python 3.10+ stdlib（`argparse`、`subprocess`、`/proc/meminfo`）、unittest、systemd user units、openspec、policy_check。

## Global Constraints

- repo：`paulsha-hippo`；profile `flat`；policy_version `1.0.12`；**tier: shareable → R-21 機密掃描**：任何檔案（含 `.md`、測試）**不得**含個人絕對路徑（`/home/<user>/…`）、機敏標記、憑證。用 `%h` / `~` / 相對路徑。
- 語言 zh-tw（PR 標題/內文/註解）。
- 分支：`feature/dream-resource-governance`（已建，off `2ef4168`）。**禁止**在 `main` 工作。
- 每個 code PR 必須更新 `CHANGELOG.md [Unreleased]`（R-09），除非標 `skip-changelog`。
- 新增 CLI 旗標必須同步 CLI help（R-16）。
- PR title 走 conventional-commit（R-10）；PR body checklist 全勾（R-11）。
- 完成前 `python3 -m policy_check --repo .` 零 failure。
- 記憶體上限用**百分比**（實體 RAM 比例），**不得**寫死機器專屬絕對數字；CPU **只用 `CPUWeight`，不設 `CPUQuota`**。
- 測試框架用 `unittest`（比照既有 `tests/test_*.py`）；跑法 `python3 -m pytest tests/ -q`。

---

### Task 1: 記憶體 headroom 述詞（lib/idle.py）

**Files:**
- Modify: `paulsha_hippo/lib/idle.py`
- Test: `tests/test_dream_idle.py`

**Interfaces:**
- Produces: `idle.has_mem_headroom(min_fraction: float = 0.20, probe: Callable[[], dict] = _read_meminfo) -> bool`；`idle._read_meminfo(path: str = "/proc/meminfo") -> dict[str, int]`。

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_dream_idle.py` 的 `DreamIdleTest` class）

```python
    def test_mem_headroom_above_threshold(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 300}))

    def test_mem_headroom_below_threshold(self):
        from paulsha_hippo.lib import idle
        self.assertFalse(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 150}))

    def test_mem_headroom_at_threshold_is_false(self):
        from paulsha_hippo.lib import idle
        # 嚴格大於：剛好 20% 不放行
        self.assertFalse(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 200}))

    def test_mem_headroom_failsafe_on_oserror(self):
        from paulsha_hippo.lib import idle

        def boom():
            raise OSError("no meminfo")

        self.assertTrue(idle.has_mem_headroom(0.20, probe=boom))

    def test_mem_headroom_failsafe_on_missing_field(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000}))

    def test_mem_headroom_zero_total_is_true(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 0, "MemAvailable": 0}))
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `python3 -m pytest tests/test_dream_idle.py -q`
Expected: FAIL —`AttributeError: module 'paulsha_hippo.lib.idle' has no attribute 'has_mem_headroom'`

- [ ] **Step 3: 實作**（append 到 `paulsha_hippo/lib/idle.py`）

```python
def _read_meminfo(path: str = "/proc/meminfo") -> dict[str, int]:
    """Parse /proc/meminfo into {field: kB}. Raises OSError if unreadable."""
    info: dict[str, int] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0])
    return info


def has_mem_headroom(
    min_fraction: float = 0.20,
    probe: "Callable[[], dict]" = _read_meminfo,
) -> bool:
    """True when MemAvailable / MemTotal > min_fraction (physical RAM, swap-free).

    MemAvailable 為可用實體 RAM 估計，本質不計 swap。讀不到/欄位缺失時 fail-safe 回 True。
    """
    try:
        info = probe()
        total = float(info["MemTotal"])
        avail = float(info["MemAvailable"])
        if total <= 0:
            return True
        return (avail / total) > float(min_fraction)
    except (OSError, KeyError, ValueError, TypeError):
        return True
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_dream_idle.py -q`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/lib/idle.py tests/test_dream_idle.py
git commit -m "feat(idle): add has_mem_headroom 記憶體 headroom 述詞"
```

---

### Task 2: dream 第二道閘 + `--min-avail-mem-pct` 旗標

**Files:**
- Modify: `paulsha_hippo/dream/cli.py:19-32`（`_run` 閘區塊）
- Modify: `paulsha_hippo/cli.py`（`dream_run` 子解析器，緊接 `--max-load` 之後）
- Test: `tests/test_dream_cli.py`

**Interfaces:**
- Consumes: `idle.has_mem_headroom`（Task 1）。
- Produces: `dream run --min-avail-mem-pct`（float，預設 20.0）；skip JSON 新增 `{"skipped": "low memory", "avail_pct": <float>, "backlog_depth": <int>}`。

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_dream_cli.py` 對應 TestCase；比照既有 `test_require_idle_busy_skips` 風格，patch `paulsha_hippo.dream.cli.idle.*`）

```python
    def test_require_idle_low_memory_skips(self):
        import io, json
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import patch
        from paulsha_hippo.dream import cli as dream_cli
        with TemporaryDirectory() as d:
            args = SimpleNamespace(
                dream_command="run", memory_root=d, now=None, dry_run=False,
                require_idle=True, max_load=1.0, min_avail_mem_pct=20.0,
                promoter="identity", agent_command=None, instruction_root=None,
            )
            with patch("paulsha_hippo.dream.cli.idle.is_idle", return_value=True), \
                 patch("paulsha_hippo.dream.cli.idle.has_mem_headroom", return_value=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = dream_cli.run(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("skipped"), "low memory")
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `python3 -m pytest tests/test_dream_cli.py::DreamCliTest::test_require_idle_low_memory_skips -q`
（class 名以檔案實際為準）
Expected: FAIL —目前無記憶體閘，會進入 pipeline 而非印 `low memory`

- [ ] **Step 3a: 在 `_run` 加第二道閘**（`paulsha_hippo/dream/cli.py`，緊接現有 `is_idle` 區塊之後、`atom_cfg = ...` 之前）

```python
    if args.require_idle and not idle.has_mem_headroom(
        getattr(args, "min_avail_mem_pct", 20.0) / 100.0
    ):
        info = idle._read_meminfo() if hasattr(idle, "_read_meminfo") else {}
        try:
            avail_pct = round(100.0 * info["MemAvailable"] / info["MemTotal"], 1)
        except (KeyError, ZeroDivisionError, TypeError):
            avail_pct = None
        print(
            json.dumps(
                {
                    "skipped": "low memory",
                    "avail_pct": avail_pct,
                    "backlog_depth": dream_ledger.backlog_depth(memory_root),
                },
                sort_keys=True,
            )
        )
        return 0
```

- [ ] **Step 3b: 加 CLI 旗標**（`paulsha_hippo/cli.py`，`dream_run.add_argument("--max-load", ...)` 之後新增一行）

```python
    dream_run.add_argument("--min-avail-mem-pct", type=float, default=20.0)
```

- [ ] **Step 4: 跑測試確認 PASS（含既有測試不回歸）**

Run: `python3 -m pytest tests/test_dream_cli.py -q`
Expected: PASS（新測試 + 既有 `test_require_idle_busy_skips` 皆綠）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/dream/cli.py paulsha_hippo/cli.py tests/test_dream_cli.py
git commit -m "feat(dream): --require-idle 加記憶體 headroom 閘 + --min-avail-mem-pct"
```

---

### Task 3: supervise 讓位 systemd timer（ops.py）

**Files:**
- Modify: `paulsha_hippo/ops.py:195-213`（`run_dream_supervise`）
- Test: `tests/test_ops.py`

**Interfaces:**
- Produces: `ops._dream_timer_active() -> bool`；`run_dream_supervise(*, interval, extra_argv=None, once=False, runner=None, timer_active=_dream_timer_active) -> int`（active 時 return 0、不呼叫 runner）。

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_ops.py` 的 supervise TestCase）

```python
    def test_supervise_defers_when_timer_active(self):
        calls = []
        rc = ops.run_dream_supervise(
            interval=1, once=True, runner=lambda: calls.append(1),
            timer_active=lambda: True,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])  # 讓位：runner 不應被呼叫

    def test_supervise_runs_when_timer_inactive(self):
        calls = []
        rc = ops.run_dream_supervise(
            interval=1, once=True, runner=lambda: calls.append(1),
            timer_active=lambda: False,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [1])
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `python3 -m pytest tests/test_ops.py -q -k supervise`
Expected: FAIL —`run_dream_supervise() got an unexpected keyword argument 'timer_active'`

- [ ] **Step 3: 實作**（`paulsha_hippo/ops.py`）

新增 helper（放在 `run_dream_supervise` 之前）：

```python
def _dream_timer_active() -> bool:
    """True 當 systemd dream timer 已接管（active）。systemctl 缺失/非 active → False。"""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", "paulsha-hippo-dream.timer"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False
    return completed.stdout.strip() == "active"
```

改 `run_dream_supervise` 簽章與前置讓位：

```python
def run_dream_supervise(*, interval: int, extra_argv: list[str] | None = None,
                        once: bool = False, runner=None,
                        timer_active=_dream_timer_active) -> int:
    """前景常駐：每 interval 秒跑一次 dream run --require-idle。

    systemd dream timer 已接管時讓位（避免雙跑）；首輪延後一個 interval。
    """
    if timer_active():
        print("systemd dream timer 已接管；supervise 讓位（不啟動前景 loop）")
        return 0
    from paulsha_hippo import cli as hippo_cli

    argv = ["dream", "run", "--require-idle", "--promoter", "llm"] + list(extra_argv or [])
    run = runner or (lambda: hippo_cli.main(list(argv)))
    while True:
        time.sleep(interval)
        try:
            run()
        except Exception as exc:  # noqa: BLE001
            print(f"dream supervise: 單輪失敗（{exc}），下一輪重試", file=sys.stderr)
        if once:
            return 0
```

- [ ] **Step 4: 跑測試確認 PASS（既有 supervise 測試不回歸）**

Run: `python3 -m pytest tests/test_ops.py -q`
Expected: PASS（新 2 測試 + 既有 `test_supervise_defers_first_run_then_invokes` / `test_supervise_survives_single_round_failure`；後兩者未傳 `timer_active`，預設 probe 在測試環境回 False 故照跑）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_ops.py
git commit -m "feat(ops): dream supervise 偵測 systemd timer 接管即讓位"
```

---

### Task 4: systemd 模板 hourly + 可攜資源上限

**Files:**
- Modify: `paulsha_hippo/dream/systemd/paulsha-memory-dream.timer`
- Modify: `paulsha_hippo/dream/systemd/paulsha-memory-dream.service`
- Test: `tests/test_dream_systemd_template.py`

- [ ] **Step 1: 改測試（RED）**——把既有 `test_timer_has_workday_morning_schedule` 改成 hourly，並加 service caps 斷言：

```python
    def test_timer_is_hourly(self):
        timer = (BASE / "systemd" / "paulsha-memory-dream.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=hourly", timer)
        self.assertNotIn("Mon..Fri", timer)
        self.assertIn("Persistent=true", timer)

    def test_service_has_portable_resource_caps(self):
        service = (BASE / "systemd" / "paulsha-memory-dream.service").read_text(encoding="utf-8")
        self.assertIn("CPUWeight=20", service)
        self.assertIn("MemoryHigh=20%", service)
        self.assertIn("MemoryMax=30%", service)
        self.assertIn("TasksMax=256", service)
        self.assertNotIn("CPUQuota", service)
```

（刪除舊的 `test_timer_has_workday_morning_schedule` 方法。）

- [ ] **Step 2: 跑測試確認 RED**

Run: `python3 -m pytest tests/test_dream_systemd_template.py -q`
Expected: FAIL —timer 還是 `Mon..Fri`、service 無 caps

- [ ] **Step 3a: 改 timer 模板**（`paulsha_hippo/dream/systemd/paulsha-memory-dream.timer`）

```ini
[Unit]
Description=Run PaulSha memory dream hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3b: 改 service 模板**（`paulsha_hippo/dream/systemd/paulsha-memory-dream.service`，在 `[Service]` 內 `ExecStart` 之前加 caps）

```ini
[Service]
Type=oneshot
# 可攜資源治理（實體 RAM 百分比；CPUWeight 為相對值，換機自動縮放）。
CPUWeight=20
MemoryHigh=20%
MemoryMax=30%
TasksMax=256
# ExecStart 一行維持原樣（--require-idle --promoter llm）。install service 會把
# paulsha-memory-dream 改名為 paulsha-hippo-dream、並把 /usr/bin/env python3 換成
# 安裝環境 interpreter，故此處 ExecStart 內容不要改。
ExecStart=/usr/bin/env python3 -m paulsha_hippo.cli dream run --memory-root %h/.agents/memory --require-idle --promoter llm
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_dream_systemd_template.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/dream/systemd/ tests/test_dream_systemd_template.py
git commit -m "feat(dream): systemd dream 改 hourly + 可攜 cgroup 資源上限"
```

---

### Task 5: 交付閘（CHANGELOG / help / policy / archive）

**Files:**
- Modify: `CHANGELOG.md`
- Verify: 全套測試、`policy_check`、openspec archive

- [ ] **Step 1: 全套測試綠**

Run: `python3 -m pytest tests/ -q`
Expected: PASS（全部）

- [ ] **Step 2: 更新 `CHANGELOG.md [Unreleased]`**（zh-tw，條列本變更；R-09）

```markdown
### Added
- dream `--require-idle` 增加記憶體 headroom 閘（`--min-avail-mem-pct`，預設 20%）。
- `hippo dream supervise` 偵測 systemd dream timer 接管即讓位（反雙跑）。

### Changed
- dream systemd timer 排程改 `OnCalendar=hourly`；service 加 `CPUWeight=20`/`MemoryHigh=20%`/`MemoryMax=30%`/`TasksMax=256`（無 CPUQuota）。
```

- [ ] **Step 3: CLI help 同步（R-16）**——若有 CLI help 快照測試，更新其 golden 使含 `--min-avail-mem-pct`；無則跳過。

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 4: policy_check**

Run: `python3 -m policy_check --repo .`
Expected: 零 failure（含 tier:shareable R-21 機密掃描——確認無個人絕對路徑/機敏標記）

- [ ] **Step 5: openspec archive**

Run: `openspec archive dream-resource-governance` 於實作完成後執行（把 change 移入 archive）。

- [ ] **Step 6: Commit CHANGELOG + archive**

```bash
git add CHANGELOG.md openspec/
git commit -m "chore(dream): CHANGELOG + openspec archive dream-resource-governance"
```

## Self-Review 註記（作者已核）

- 覆蓋：spec 五個 ADDED requirement 各對應 Task 1（mem 閘）、Task 2（旗標+skip）、Task 3（讓位）、Task 4（hourly+caps）。
- 無 placeholder：每步含實際程式碼/指令/預期輸出。
- 型別一致：`has_mem_headroom(min_fraction, probe)`、`_read_meminfo()`、`run_dream_supervise(..., timer_active=...)`、`_dream_timer_active()` 跨 task 一致。
- 相容：`_run` 用 `getattr(args, "min_avail_mem_pct", 20.0)` 防手建 args 缺欄位；`timer_active` 預設 probe 在無 systemd/CI 回 False 故既有 supervise 測試不回歸。
