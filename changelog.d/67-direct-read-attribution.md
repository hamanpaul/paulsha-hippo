---
type: fix
---
- 未經 offer 的「直讀」（agent 讀取一個當下沒被 shortlist 給它的 knowledge 檔）不再被
  `collect_usage_reads()` 整批丟棄。實測真實 ledger 30 天窗內 600 筆 read 中有 550 筆屬此類，
  這些 slice 先前在 janitor retention 的 `last_read_at` 上等同從未被讀過而可能誤 decay；
  現在直讀照常更新 `last_read_at`（retention 覆蓋的 slice 由 31 增為 572），但**不**計入
  `read_count`，因此 search usage boost 與 `hippo usage funnel` 的 read-through 主指標
  語意完全不變（維持 31 slice／50 events）。
- `read_without_offered` 移出 `DIAG_KEYS`，改以獨立且同樣 bounded 的 `STAT_KEYS`
  （`direct_read`）計數。janitor 會把任何非零 *diagnostic* 升為 warning、進而讓
  `hippo dream run` 整輪降為 `partial`——直讀是正常 agent 行為，不該有此效果。此為 #64
  同型缺陷（良性狀態被當錯誤訊號釘死 dream）的第二個來源。真實 memory root 實測：
  janitor `warnings` 由 `["usage ledger diagnostics: {'read_without_offered': 550}"]` 轉為 `[]`。
  時間戳壞損、identity 缺漏、未來與窗口外事件仍屬 diagnostics，照常 warning。
