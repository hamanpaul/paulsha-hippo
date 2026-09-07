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

### Requirement: hippo show --agent 的精簡輸出與 read 歸因

系統 SHALL 提供 `hippo show <slice_id|path> [--memory-root <root>] [--agent] [--tool <tool> --session-id <sid>]`。`--agent` 輸出 SHALL 只含 ≤ 8 行 header（title、slice_id、project、captured_at、artifact_kind、supersedes、`commit (commit_source)`、cites）與 slice body，MUST NOT 含 `distiller`、`checksum`、`publication_id`、attempts 或其他 provenance 機器欄位；輸出 bytes SHALL ≤ body 長度 + 400。同時給定 `--tool` 與 `--session-id` 時，`show` SHALL 以與 PostToolUse Read hook 相同的 schema append 一筆 read 事件至 `runtime/ledger/memory_usage.jsonl`（`ts`、`session_id`、`tool`、`project`、`sl_id`、`path`、`source:"read"`、`offered`），`offered` 依該 session 的 offered 映射 `by_id` 判定；未給定兩參數時 MUST NOT 寫事件。歸因失敗（映射缺失、ledger 不可寫等）MUST NOT 影響輸出且 exit 0。經 `show` 記錄的 read SHALL 與 Read hook 的 read 同等計入 KPI 看過率（以 unique note 計，同一 slice 兩種來源不重複計數）。

#### Scenario: agent 輸出精簡且不含機器欄位
- **WHEN** 對一筆含完整 `distiller:` 區塊的 slice 執行 `hippo show <slice_id> --agent`
- **THEN** stdout MUST NOT 含 `distiller`／`checksum`／`publication_id`，且 bytes ≤ body + 400

#### Scenario: 帶 tool 與 session 時記 read 事件
- **WHEN** 以 `--tool claude-code --session-id S` 執行，且該 slice 在 session S 的 offered 映射中
- **THEN** `memory_usage.jsonl` SHALL 多一筆 `source:"read"`、`offered:true`、帶該 `sl_id`/`path` 的事件；不在映射中時 `offered:false`

#### Scenario: 不帶歸因參數不寫事件
- **WHEN** 只執行 `hippo show <slice_id> --agent`
- **THEN** `memory_usage.jsonl` MUST NOT 新增事件，輸出仍完整

#### Scenario: 歸因失敗不影響輸出
- **WHEN** ledger 目錄不可寫或 offered 映射損毀
- **THEN** stdout 仍為完整精簡輸出，命令 exit 0
