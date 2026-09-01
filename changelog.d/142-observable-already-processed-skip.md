---
type: fix
---
- 修 issue #142：`paulsha_hippo/atomizer/pipeline.py::_split_pass()` 對「session_key 已有處理狀態」（`split`／`parked`，或 `promoted`／`no-findings` 且 inbox 內容 hash 未變）的兩個 skip 分支完全靜默——`continue` 前不寫 `warnings`、不累加任何 counter。生產實證：一份已由該 session 自己的 hook import 處理過的 inbox 文件被重新丟進 `hippo atomize`，回傳 `slices: 0, skipped: 0, warnings: []`，外觀上跟「inbox 空的」一模一樣，實際上文件被默默略過、原封不動留在 inbox。
- 兩個分支現在都補上具名 `warnings` 項目（含檔案路徑、`session_key`、既有狀態）與獨立的 summary counter `skipped_already_processed`（與既有涵蓋所有跳過原因的 `summary.skipped` 分開）。`_split_pass()` 回傳值新增第三個元素（`count, dry_run_fragments, skipped_already_processed`），呼叫端 `run()` 已同步更新；跳過語意本身未變（idempotency 仍是對的，只是從靜默變成可觀測）。
- 新增兩個回歸測試（`tests/test_atomizer_pipeline.py`）：`test_redispatch_with_existing_split_state_is_observable_not_silent`（`split` 分支）與 `test_redispatch_with_unchanged_promoted_content_is_observable_not_silent`（`promoted`＋hash 未變分支），各自預先寫入處理狀態後重跑 `pipeline.run()`，斷言 `summary.skipped_already_processed == 1`、`warnings` 內出現點名該路徑／session_key／狀態的訊息，且原始 inbox 文件維持存在、未被移動。
