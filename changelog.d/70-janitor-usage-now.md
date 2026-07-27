### Fixed
- dream 執行期間、其他 agent 對同一 memory root 的併發 ledger 寫入不再被 janitor
  誤判為 `future_event`、令整輪降為 `partial`。`dream/orchestrator.py` 以單一 `now`
  釘住整輪並先跑 atomize（已知的 rglob 效能問題可讓單輪耗時 10+ 分鐘），janitor 隨後
  沿用同一個 run-start 的 `now` 判斷 usage ledger 事件是否「來自未來」；任何在 run 起點
  之後、janitor 實際讀檔之前寫入的 offered/read 事件（例如 prompt-time hook 每次送出
  提示都會寫一筆 offered）因此被誤記為時鐘偏移。實測一輪 `now=07:03:27Z`、atomize 跑
  13 分鐘的 run 中，`offered.jsonl` 於 07:05–07:16 間新增 4 筆同期 session 事件，其中
  2 筆落入 janitor 讀檔前即造成 `usage ledger diagnostics: {'future_event': 2}` warning，
  使該輪整體降為 `partial`。
- `janitor.scanner.run_scan` 新增 keyword-only 參數 `usage_now: str | None = None`：
  usage ledger（offered/read）診斷改以此為時間基準，預設為 janitor 實際執行當下的
  牆鐘時間（而非 run 起點的 `now`）；`now` 既有的 TTL/decay/reactivation 判定與
  lifecycle 事件 ts 記錄完全不受影響。dream CLI／`hippo janitor scan` 呼叫端不需改動
  即得到修復——併發寫入的 ts 只要不晚於 janitor 實際讀檔時刻即不再算 `future_event`；
  真正的時鐘偏移（ts 晚於 janitor 實際讀檔時刻）仍照常記診斷、照常 warning。
  此為「良性狀態被當錯誤訊號釘死 dream」反模式的第三個實例（前兩例：#64 ledger 撕裂行、
  #67 `read_without_offered` 直讀）。
