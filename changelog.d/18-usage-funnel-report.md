### Added

- 新增 `hippo usage funnel` session 層級漏斗報表，以收到非空記憶 brief 的 session 為分母，統計 offered → read → applied 的轉換率，並提供 per-tool 與被讀取 slice 的 top-N 特徵；支援 `--since`、`--json` 及既有／新版 offered ledger 格式。

### Changed

- 報表同時列出含噪音與排除噪音兩組數字；排除組依唯讀 fold 的 `processing.jsonl` 最終 `state=no-findings` 排除 session，預設以排除組作為主指標，並可用 `--include-noise` 切換。
- `read-through` 只計同一 `tool:session_id` 曾被 offer 的相同 slice 且在 offer 後發生的 read；未經 offer 的直讀另列於 attribution 統計，不再灌入主指標。

- 主指標 read-through 只計「同 session 先 offer、後 read」的事件；未經 offer 的直讀另列，不混入轉換率。
