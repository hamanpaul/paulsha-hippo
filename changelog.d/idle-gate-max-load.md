---
type: fix
---
- dream `--require-idle` 的 `--max-load` 預設由 `1.0` 放寬為 `4.0`：這台機器
  20 核，1 分鐘 loadavg 上限 1.0 等於只准 5% 總負載，實測近 5 天內有新進料的
  時段有 29% 下一輪被這個閘門跳過——閘門專打「有工作發生」的時段，恰與 dream
  service 存在的目的相反。cgroup 已有第二層資源保護（unit 的
  `CPUWeight=20`、`MemoryHigh=20%`），`--max-load` 不需要單獨扛住全部壓力；
  `paulsha_hippo/lib/idle.py` 的 `is_idle()` 函式參數預設維持 `1.0` 不動，
  只調整 `hippo dream run` 的 CLI 層預設，保持函式本身通用。systemd service
  未顯式帶 `--max-load`，改版後直接吃到新預設，不需改 unit 檔。
- `hippo dream run` 的 system busy skip JSON 補上觀測值 `load`（1 分鐘
  loadavg，取到小數 2 位；量不到時為 `null`），修補原本「被判忙碌但看不到當
  下 load 是多少」的診斷盲區；low memory skip 原本就有 `avail_pct`，未變動。
  新增 `paulsha_hippo/lib/idle.py::read_load1()` 提供這個讀值，語意與
  `is_idle()` 呼應但不共用回傳型別：讀不到一律回 `None`（而非折成布林值），
  避免顯示用途的失敗又混進閘門判斷。
