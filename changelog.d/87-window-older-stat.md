---
type: fix
---
- `window_older`（offered/read 事件的 ts 早於 30 天分析窗口）不再被
  `collect_usage_reads()` 歸類為 diagnostic。`offered.jsonl`／`memory_usage.jsonl`
  是 append-only ledger，事件隨真實時間推進而老化出窗是必然結果，不是缺陷——但
  janitor 會把任何非零 *diagnostic* 升為 warning，進而讓 `hippo dream run` 整輪降為
  `partial`。實測：`offered.jsonl` 最舊事件為 2026-06-29、窗口 30 天，`window_older`
  在自 2026-07-29T09:00Z 起的每輪 dream 中以 10→15 單調增長，是該時段唯一的 warning
  來源，使每輪皆被誤判為 `partial`。
- 修法：`window_older` 移出 `DIAG_KEYS`，改記入與 `direct_read`（issue #67）同樣
  bounded 的 `STAT_KEYS`，兩個計數點（offered 事件窗口外、read 事件窗口外）皆改寫
  `stats["window_older"]` 而非 `diagnostics["window_older"]`；計數與 `collect_usage_reads()`
  的第三個回傳值本身不受影響，可觀測性不變，只是分類改變——janitor 不再據此產生
  warning。此為「良性狀態被當錯誤訊號釘死 dream」反模式的第四例（前三例：#64 ledger
  撕裂行、#67 `direct_read`、#71 `future_event`）。
