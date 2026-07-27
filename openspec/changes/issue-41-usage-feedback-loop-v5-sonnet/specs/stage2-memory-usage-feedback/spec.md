## Lifecycle mapping

- Active change：`openspec/changes/issue-41-usage-feedback-loop-v5-sonnet`
- Lifecycle base：`b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`
- Supersedes：`openspec/changes/issue-41-usage-feedback-loop-v4`
- Scope spec：`stage2-memory-usage-feedback`

## ADDED Requirements

### Requirement: usage-ledger streaming 與 fail-soft 邊界

The usage aggregation SHALL 使用逐行 iterator 載入 ledger，並對 I/O、UTF-8、單行 parse 錯誤採 fail-soft，不中止整體流程。

#### Scenario: streaming / regression 回歸

- **WHEN** `runtime/ledger/offered.jsonl` 或 `runtime/ledger/memory_usage.jsonl` 使用超大檔、或 `Path.read_text` 被 monkeypatch 成拋例外
- **THEN** 解析流程必須改為逐行 iterator，不得一次載入 list；filesystem
  metadata/open/read/decode 錯誤採 fail-soft，逐行 decode 且繼續處理其餘
  合法行，並且不改寫 ledger。

### Requirement: invalid rows 與 bounded diagnostics

The implementation SHALL 排除 malformed/non-object / invalid read/offered 事件，僅以有界 counter 作為診斷輸出，並不得影響合法事件。

#### Scenario: malformed 與無效事件

- **WHEN** 出現 malformed JSON、non-object、缺少或空白 `tool/session/sl_id`、`invalid`、`future`、`out-of-window`
- **THEN** 該事件不得 cross-match，只能計入固定 key 的有界 counter，且不影響合法事件處理。

### Requirement: identity 與時間窗匹配

Attribution SHALL 僅接受 `(tool, logical session, sl_id)` 完整且先行 offer、且符合時間窗的 read；否則歸入未歸因統計。

#### Scenario: offered/read 對應規則

- **WHEN** read 事件對應的 `(tool, logical_session, sl_id)` 缺漏或為 `(unknown)`，或 offered/read 任一時間為 future/out-of-window，或 offered 時間晚於 read
- **THEN** 該 read 不得歸因到 slice，且不參與 `read_count/last_read_at` 聚合。

### Requirement: usage 時間基準可注入

Index build SHALL 接受可注入的 UTC 時間基準與窗口，並原樣傳至 usage
aggregation；未注入時才使用目前 UTC 與預設窗口。

#### Scenario: dream/index rebuild 可重現

- **WHEN** caller 提供 `usage_now` 與 `usage_window_days`
- **THEN** 相同 ledger 與相同輸入必須產生相同 `read_count/last_read_at`，
  且 aggregation 必須使用 caller 提供的值。

### Requirement: ranking 穩定性與 0.04 上限

The ranking engine SHALL 使用 stable key 搭配 0.04 上限，且無 boost 時保留 legacy 行為。

#### Scenario: boost 與排序

- **WHEN** 有 `read_count` 的 slice 需要計分
- **THEN** 使用 `usage_boost = min(0.04, 0.01 * log2(1 + read_count))`，無 boost 時走 legacy fast path；有 boost 時 stable key `(adjusted_score, base_score, slice_id)`，且 `base_score` 間距超過 `0.04` 時不得反轉。

#### Scenario: 無 boost 維持 legacy stable order

- **WHEN** index 不含 usage 欄位，或結果集沒有任何正 `read_count`
- **THEN** 排序只使用既有 base-score key，base-score 同分保持原輸入順序，
  不得新增 raw BM25 或 slice id tie-break。

### Requirement: index compat / legacy fallback

Index build SHALL 以 temp DB 重建；新欄位缺省值 SHALL 透過 schema introspection fallback 補值。

#### Scenario: 舊 DB 不中斷

- **WHEN** `slice_meta` 已是舊結構
- **THEN** 以 schema introspection fallback 提供預設 `read_count=0`、`last_read_at=NULL`，並保留 flock、temp、atomic replace 行為。

### Requirement: janitor 優先序與 retention

janitor `scan` SHALL 維持 `superseded`、`source_invalid`、`ttl` 的決策優先順序，並 SHALL 輸出可觀測欄位（含 `ttl_base`、`source`）。

#### Scenario: superseded/source_invalid/ttl 順序

- **WHEN** slice 同時觸發 `superseded` 與 `ttl`、或有 `source_invalid`
- **THEN** `superseded` / `source_invalid` 優先於 `ttl`，`read` 不得 re-activate 已 decayed 的 slice；`ttl` 計算源自 `max(captured_at, active_since_ts, valid last_read_at)`，future/malformed `last_read_at` fail-closed，detail 記錄 `ttl_base` 與 `source`。

### Requirement: scanner 可觀測性與零值噪訊

Scanner SHALL 只在 counter > 0 時輸出 warning，對零值維持靜默。

#### Scenario: no-zero warning

- **WHEN** 掃描器統計 counter 為零
- **THEN** 不輸出多餘的零值 warning；當 counter > 0 才輸出對應 warning 並保留固定 key。

### Requirement: docstring/churn 可讀性

The implementation SHALL 維護模組 docstring 真實性與回報欄位穩定性，避免非必要格式變更。

#### Scenario: `__doc__` 保持

- **WHEN** 實作涉及模組說明更新
- **THEN** 必須維持 `__doc__` 真實內容，不得引入無關格式 churn。

### Requirement: 交付需 exact-head 與 frozen plan 閱讀

交付流程 SHALL 以同一個 exact candidate head 包含本 change authority、
regressions 與實作，供 current-head review 一次性審閱。

#### Scenario: current-head review 前置

- **WHEN** candidate 準備開 PR 或 merge
- **THEN** reviewer 必須先閱讀 frozen plan 與 authority，再以 current head
  的 code、tests、CI 與 review threads 作結論。
