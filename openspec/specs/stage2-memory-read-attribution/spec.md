# stage2-memory-read-attribution Specification

## Purpose
TBD - created by archiving change stage2-memory-consumption-loop. Update Purpose after archive.
## Requirements
### Requirement: read-based usage 歸因（PostToolUse）

系統 SHALL 提供一個註冊於 PostToolUse、matcher 為 `Read` 的 hook。當該次 Read 的目標路徑位於 memory knowledge 層（`<memory_root>/knowledge/` 之下）時，hook SHALL append 一筆 read-based `used` 事件至 `runtime/ledger/memory_usage.jsonl`，含 `ts`、`session_id`、`tool`、`project`、`sl_id`、`path`、`source:"read"`、`offered`(bool)；`offered=true` 當該路徑命中本 session 的 offered 映射，否則 `offered=false`（agent 自行找到）。hook SHALL 為 best-effort：路徑不在 knowledge 層、offered 映射缺失或任何例外時，MUST NOT 寫事件、MUST NOT 干擾 Read 本身，且 exit 0。

#### Scenario: Read 被推送的 knowledge 路徑記為 used(offered)
- **WHEN** agent 對某個曾於本 session 短清單中被 offered 的 knowledge 絕對路徑執行 Read
- **THEN** `memory_usage.jsonl` SHALL 新增一筆 `source:"read"`、`offered:true` 且帶該 `sl_id`/`path` 的事件

#### Scenario: Read 非 offered 的 knowledge 路徑記為 used(offered=false)
- **WHEN** agent Read 一個位於 knowledge 層但未被本 session offered 的路徑
- **THEN** SHALL 新增一筆 `source:"read"`、`offered:false` 的事件

#### Scenario: Read 非 knowledge 路徑不記事件
- **WHEN** agent Read 一個不在 `<memory_root>/knowledge/` 之下的檔
- **THEN** MUST NOT 寫任何 used 事件

#### Scenario: 任一錯誤不干擾 Read
- **WHEN** 歸因過程發生任何例外（缺映射、解析失敗等）
- **THEN** hook SHALL log warning、不寫事件、exit 0，Read 結果不受影響

### Requirement: 歸因對齊以路徑優先、sl_id 回退

read 歸因對齊 SHALL 以絕對路徑（realpath）為主鍵比對 offered 映射；當 offered 的舊路徑因 janitor rename/move 與當前 Read 路徑不符時，SHALL 以 `sl_id`（由路徑反查或映射回退）對齊，避免漏記。

#### Scenario: rename 後的 slice 仍可歸因
- **WHEN** 某 slice 在 offered 後被 janitor 改名，agent Read 其新路徑
- **THEN** hook SHALL 透過 sl_id 回退對齊，仍記為該 slice 的 used 事件

