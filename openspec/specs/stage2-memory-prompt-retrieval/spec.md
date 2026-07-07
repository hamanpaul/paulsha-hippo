# stage2-memory-prompt-retrieval Specification

## Purpose
TBD - created by archiving change stage2-memory-consumption-loop. Update Purpose after archive.
## Requirements
### Requirement: 任務條件式短清單注入（UserPromptSubmit）

memory prompt-retrieval hook SHALL 於 UserPromptSubmit 解析 session 的 project、由當前 prompt 建構 FTS 查詢、對該 project 跑既有 bm25 檢索（`search.py`）、套用 relevance gate，並將 top-k（預設 k=3、上限 5）結果以**短清單**注入為 additional context：每列為「標題 · 一行摘要 · 該 slice 的絕對路徑」，並附「相關項用 Read 開啟路徑取全文」提示。hook SHALL 為 best-effort：project 無法解析、index 不存在、無命中或任何例外時，MUST NOT 注入、MUST NOT 阻斷或干擾 prompt，且 exit 0。

#### Scenario: 相關 prompt 產生短清單
- **WHEN** 在可解析 project 的 session 送出與某些 knowledge slice 相關的 prompt，且檢索有過 gate 的命中
- **THEN** hook SHALL 注入 ≤k 列短清單，每列含標題、一行摘要與該 slice 的絕對路徑，並含 Read 提示

#### Scenario: trivial 或無命中 prompt 不注入
- **WHEN** prompt 經 `to_fts_query` 後為空（如 `/effort`、純標點）或檢索無過 gate 命中
- **THEN** hook MUST NOT 注入任何短清單，prompt 照常進行

#### Scenario: 未知 project 或 index 缺失不注入
- **WHEN** cwd 解析為 `_unknown`，或 `retrieval.db` 不存在
- **THEN** hook MUST NOT 注入，且 exit 0

#### Scenario: 任一錯誤不干擾 prompt
- **WHEN** 檢索或注入過程發生任何例外
- **THEN** hook SHALL log warning、不注入、exit 0，prompt 不受影響

### Requirement: FTS 查詢淨化純函式

系統 SHALL 提供純函式 `to_fts_query(prompt)`，自任意 prompt 文字抽出 alnum/CJK token 並以 OR 連接成合法 FTS5 查詢，使任意 prompt（含 FTS5 特殊字元 `"`、`*`、`(`、`-` 等）MUST NOT 觸發 FTS5 語法錯誤。空字串或無可用 token 時 SHALL 回空字串（由呼叫端視為不檢索）。函式 MUST 為純函式、不丟例外。

#### Scenario: 特殊字元被淨化不致語法錯誤
- **WHEN** 對含 `"`、`*`、`(`、`AND` 等 FTS5 敏感字元的 prompt 呼叫 `to_fts_query`
- **THEN** 回傳的查詢字串用於 `slices_fts MATCH ?` MUST NOT 引發 `sqlite3.OperationalError`

#### Scenario: 空或無 token 回空
- **WHEN** prompt 為空白或僅標點
- **THEN** `to_fts_query` SHALL 回空字串

### Requirement: per-prompt 記錄 offered 與對齊映射

對每次注入的短清單，hook SHALL append 一筆 offered 記錄至持久 ledger（含 `session_id`、`project`、`ts`、`offered`=該批 `{sl_id, path}` 陣列），並維護 per-session 的 `sl_id ↔ 絕對路徑` 雙向映射（`runtime/wakeup/<tool>__<sid>.offered.json`，跨本 session 多次 prompt 累積），供 read 歸因對齊。寫入失敗 MUST NOT 影響注入或 prompt（best-effort）。

#### Scenario: offered 落地含 id 與路徑
- **WHEN** 某次 UserPromptSubmit 注入了短清單
- **THEN** ledger SHALL 新增一筆含該批 `{sl_id, path}` 的 offered 記錄，且 per-session 映射 SHALL 含各 sl_id↔path

#### Scenario: 未注入則不記 offered
- **WHEN** 該 prompt 未注入短清單（無命中／未知 project）
- **THEN** MUST NOT 寫 offered 記錄

### Requirement: session 內去重（同 slice 不重複 offer）

prompt-retrieval hook SHALL 於注入前讀回 per-session offered 映射（`runtime/wakeup/<tool>__<sid>.offered.json` 之 `by_id`），過濾本 session 已 offer 過的 `sl_id` 後才注入。檢索端 SHALL 過取候選（fetch 上限 > k），使過濾後仍能以次佳候選補位至 k 筆。過濾後無剩餘候選時 MUST NOT 注入、MUST NOT 追加 offered 記錄（維持「未注入不記錄」不變量，offered 分母不灌水）。映射檔缺失或損毀時 SHALL fail-open（視為空集合、照常 offer），且任何讀取錯誤 MUST NOT 阻斷或干擾 prompt。去重範圍為單一 session：新 session MUST NOT 受先前 session 的 offered 影響。offered 映射的檔案路徑 SHALL 由讀寫兩端共用之單一函式產生（防 drift）。

#### Scenario: 重複 prompt 以次佳補位

- **WHEN** 同 session 先後送出兩個相關 prompt，且第一次已 offer 了最佳候選，池內仍有其他相關候選
- **THEN** 第二次注入 MUST NOT 含第一次已 offer 的 `sl_id`，且 SHALL 以次佳候選補位注入

#### Scenario: 候選枯竭不注入不記錄

- **WHEN** 同 session 再次送出相關 prompt，但所有相關候選皆已 offer 過
- **THEN** hook MUST NOT 注入任何短清單，且 offered ledger 與 per-session 映射 MUST NOT 新增記錄

#### Scenario: 新 session 不受舊 session 去重影響

- **WHEN** 另一個 session 送出相同 prompt
- **THEN** hook SHALL 照常注入該 session 尚未 offer 過的候選

#### Scenario: 映射損毀 fail-open

- **WHEN** per-session offered 映射檔內容非法（無法 parse）
- **THEN** hook SHALL 視為空集合照常注入，MUST NOT 拋出例外或干擾 prompt

### Requirement: 短清單摘要行資訊量（跳過標題重複行）

短清單摘要 SHALL 取 slice body 中第一個「有資訊」行：跳過 YAML frontmatter、空白行，以及正規化後與該 slice `title` 相同之行（正規化 SHALL 忽略大小寫、空白、標點與底線差異，例如 `# Overview` ≈ `overview`、`Review Summary` ≈ `review-summary`）。全部行皆為標題重複（或 body 無可用行）時，摘要 SHALL 為空字串——注入列仍含標題與絕對路徑，MUST NOT 以標題重複行充當摘要。

#### Scenario: 首行為標題重複時取下一個有資訊行

- **WHEN** 某 slice `title` 為 `overview`，body 首行為 `# Overview`、次行為具體技術結論
- **THEN** 注入摘要 SHALL 為該具體技術結論行，MUST NOT 為 `Overview`

#### Scenario: 全部為標題重複時摘要為空

- **WHEN** 某 slice body 僅含與 title 正規化後相同的行
- **THEN** 摘要 SHALL 為空字串，注入列仍含標題與路徑

#### Scenario: 首行非標題重複時行為不變

- **WHEN** 某 slice body 首行為與 title 不同的實質內容
- **THEN** 摘要 SHALL 為該首行（與既有行為一致）

