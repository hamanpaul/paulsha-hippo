## 1. 記憶體 headroom 閘（lib/idle.py）

- [ ] 1.1 RED：`tests/test_dream_idle.py` 加 `has_mem_headroom` 測試——高於門檻回 True、低於回 False、等於門檻回 False（嚴格大於）、probe 拋例外 fail-safe 回 True、`MemTotal<=0` 回 True。先跑確認因缺函式而 RED。
- [ ] 1.2 GREEN：`paulsha_hippo/lib/idle.py` 實作 `_read_meminfo()`（解析 `/proc/meminfo` 為 dict）與 `has_mem_headroom(min_fraction=0.20, probe=_read_meminfo)`；讀不到/欄位缺失 fail-safe 回 True。跑綠。

## 2. dream/cli.py 第二道閘 + CLI 旗標

- [ ] 2.1 RED：`tests/test_dream_cli.py` 加測試——`--require-idle` 下注入低記憶體 probe → 輸出 `skipped: "low memory"` 且不進 pipeline；記憶體足 + load 足 → 進 pipeline。RED。
- [ ] 2.2 GREEN：`paulsha_hippo/dream/cli.py::_run` 於 load 閘之後加記憶體閘（呼叫 `idle.has_mem_headroom(args.min_avail_mem_pct/100)`），skip JSON 含 `skipped:"low memory"`、`avail_pct`、`backlog_depth`。
- [ ] 2.3 GREEN：`paulsha_hippo/cli.py` 於 `dream run` 子解析器加 `--min-avail-mem-pct`（`type=float, default=20.0`）。跑綠。

## 3. supervise 讓位 systemd（ops.py）

- [ ] 3.1 RED：`tests/test_ops.py` 加測試——`run_dream_supervise(timer_active=lambda: True, ...)` → return 0、不呼叫 runner；`timer_active=lambda: False, once=True` + 假 runner → runner 被呼叫一次。RED。
- [ ] 3.2 GREEN：`paulsha_hippo/ops.py` 加 `_dream_timer_active()`（`systemctl --user is-active paulsha-hippo-dream.timer` == `"active"`，`systemctl` 缺失回 False），`run_dream_supervise` 新增 `timer_active` 參數，active 即印讓位訊息並 return 0。跑綠。

## 4. systemd 模板（hourly + 資源上限）

- [ ] 4.1 RED：`tests/test_dream_systemd_template.py` 改寫既有 `Mon..Fri` 斷言為 `OnCalendar=hourly`；加 service 含 `CPUWeight`/`MemoryHigh`/`MemoryMax`/`TasksMax`、不含 `CPUQuota` 的斷言。RED。
- [ ] 4.2 GREEN：改 `paulsha_hippo/dream/systemd/paulsha-memory-dream.timer` 為 `OnCalendar=hourly`（保 `Persistent=true`）；`...-dream.service` `[Service]` 加 `CPUWeight=20`/`MemoryHigh=20%`/`MemoryMax=30%`/`TasksMax=256`。跑綠。

## 5. 交付閘（hippo policy v1.0.12）

- [ ] 5.1 全套測試綠：`python3 -m pytest tests/ -q`（或 repo 指定指令）。
- [ ] 5.2 `CHANGELOG.md [Unreleased]` 補 entry（R-09）。
- [ ] 5.3 R-16 CLI help 同步（若有 help 快照測試一併更新）。
- [ ] 5.4 `python3 -m policy_check --repo .` 零 failure；tier:shareable 機密掃描（R-21）通過——不得含個人絕對路徑/機敏標記。
- [ ] 5.5 conventional-commit；PR body checklist 全勾；zh-tw。
