# stage2-memory-usage-telemetry Specification

## Purpose
TBD - created by archiving change stage2-memory-usage-telemetry. Update Purpose after archive.
## Requirements
### Requirement: usage 訊號擷取純函式

系統 SHALL 於 `paulsha_hippo/usage.py` 保留純函式 `extract_offered(brief)`（從含 `[[stem--sl-id|title]]` wikilink 的文字抽 `(slice_id, title)`），供相容與工具用途。`extract_cited` / `extract_matched`（逐字 `sl-id` 回吐 / 標題逐字命中）SHALL 標記為 **deprecated、非規範**：二者 MUST NOT 再作為 `used` 主訊號（主訊號改為 read-based 歸因，見 `stage2-memory-read-attribution`）；保留僅為向後相容，得於後續另案移除。所有保留函式 MUST 為純函式、對畸形輸入回空集合而不丟例外。

#### Scenario: extract_offered 仍可從 wikilink 抽 offered
- **WHEN** 對含 `[[foo--sl-abc...|標題]]` 的文字呼叫 `extract_offered`
- **THEN** 回傳含 `(sl-abc..., 標題)` 的清單

#### Scenario: cited/matched 不再驅動 used 主訊號
- **WHEN** 計算某 session 的 `used`
- **THEN** 系統 SHALL 採 read-based 歸因事件，MUST NOT 以 `extract_cited`/`extract_matched` 作為 used 來源

### Requirement: usage 查詢 CLI

系統 SHALL 提供 `hippo usage`（`--memory-root`、`--since`、`--json`）讀取兩個 ledger：
`runtime/ledger/offered.jsonl`（offered 事件權威，prompt-time shortlist 與 `hippo recall` 寫入）
與 `runtime/ledger/memory_usage.jsonl`（read-based `used` 事件 `source:"read"`、applied 事件
`kind:"applied"`），聚合出每 slice 的 `offered_count / read_count / last_read`（依 read 降冪）、
彙總（總 session、平均每 session read、never-read 數＝offered 過但 read=0），以及 per-tool 的
`offered / read / applied` 分列。`applied` 欄於該 tool 無任何 applied 事件時 SHALL 顯示
`n/a`（JSON 為 `null`），MUST NOT 以內容 substring 猜測補值。`read_count` SHALL 來自
read-based `used` 事件（`source:"read"`）。即使 `runtime/wakeup/*.json` 全不存在，報告
SHALL 正確（ledger 自足）。

系統 SHALL 提供 `hippo usage mark-applied`（`--memory-root`、`--session-id`、`--slice-id`、
`--tool` 皆必填）作為 applied 顯式訊號入口（agent structured acknowledgement）。寫入前
SHALL 驗證參照完整性：同 `(session_id, tool)` 於 `runtime/ledger/offered.jsonl` 存在
先行 offered 記錄、且 `slice_id` 屬於該些 offer 的 slice 集合；通過才 append 事件
`{"kind":"applied","session_id","slice_id","tool","ts"}` 至
`runtime/ledger/memory_usage.jsonl`；驗證失敗 SHALL exit 非零、MUST NOT 寫入任何事件，
並於 stderr 說明原因（防偽造 applied 事件污染漏斗遙測）。

#### Scenario: offered-but-unread 計入 never-read 且報告自足
- **WHEN** 某 slice 在 ledger 多次 offered 但從未有 read 事件，且 wakeup 檔已不存在
- **THEN** `hippo usage` SHALL 列出該 slice（offered_count>0、read_count=0）並計入 never-read 彙總

#### Scenario: read 事件計入 read_count
- **WHEN** ledger 含某 slice 的 `source:"read"` used 事件
- **THEN** 該 slice 的 `read_count` SHALL ≥1 且 `last_read` SHALL 反映最近事件時間

#### Scenario: applied 顯式訊號計入 per-tool 分列
- **WHEN** ledger 含某 tool 的 `kind:"applied"` 事件
- **THEN** per-tool 分列中該 tool 的 `applied` SHALL 為事件計數；無 applied 事件的 tool SHALL 顯示 `n/a`（JSON `null`）

#### Scenario: 偽造 applied 事件被拒
- **WHEN** `mark-applied` 指向的 `(session_id, tool)` 無先行 offered 記錄，或 `slice_id` 不在該些 offer 的 slice 集合內
- **THEN** 命令 SHALL exit 非零、MUST NOT append 任何事件，且 stderr SHALL 說明拒絕原因
