## ADDED Requirements

### Requirement: hippo show --agent 的精簡輸出與 read 歸因

系統 SHALL 提供 `hippo show <slice_id|path> [--memory-root <root>] [--agent] [--tool <tool> --session-id <sid>]`。`--agent` 輸出 SHALL 只含 ≤ 6 行 header（title、slice_id、project、captured_at、artifact_kind、supersedes、`commit (commit_source)`、cites）與 slice body，MUST NOT 含 `distiller`、`checksum`、`publication_id`、attempts 或其他 provenance 機器欄位；輸出 bytes SHALL ≤ body 長度 + 400。同時給定 `--tool` 與 `--session-id` 時，`show` SHALL 以與 PostToolUse Read hook 相同的 schema append 一筆 read 事件至 `runtime/ledger/memory_usage.jsonl`（`ts`、`session_id`、`tool`、`project`、`sl_id`、`path`、`source:"read"`、`offered`），`offered` 依該 session 的 offered 映射 `by_id` 判定；未給定兩參數時 MUST NOT 寫事件。歸因失敗（映射缺失、ledger 不可寫等）MUST NOT 影響輸出且 exit 0。經 `show` 記錄的 read SHALL 與 Read hook 的 read 同等計入 KPI 看過率（以 unique note 計，同一 slice 兩種來源不重複計數）。

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
