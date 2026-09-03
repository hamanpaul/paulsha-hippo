# stage2-memory-prompt-retrieval Specification

## Purpose
TBD - created by archiving change stage2-memory-consumption-loop. Update Purpose after archive.
## Requirements
### Requirement: 任務條件式短清單注入（UserPromptSubmit）

memory prompt-retrieval hook SHALL 於 UserPromptSubmit 解析 session 的 project、由當前 prompt 建構 FTS 查詢、對該 project 跑既有 bm25 檢索（`search.py`）、套用 relevance gate，並將 top-k（預設 k=3、上限 5）結果以**短清單**注入為 additional context：每列為「標題 · 一行摘要 · 該 slice 的絕對路徑 · `slice_id`」。清單後的取用提示 SHALL 依 `shortlist.read_hint` 切換：`show`（預設）時提示「相關項用 `hippo show --agent <slice_id> --tool <tool> --session-id <session_id>` 取內容」且命令中的 tool／session_id SHALL 由 hook 填好；`read` 時維持「相關項用 Read 開啟路徑取全文」。無 prompt-time hook 平台的 recall 指引（`_wakeup_common.recall_guidance_hint`）SHALL 同步使用相同的 hint 模式。hook SHALL 為 best-effort：project 無法解析、index 不存在、無命中或任何例外時，MUST NOT 注入、MUST NOT 阻斷或干擾 prompt，且 exit 0。

#### Scenario: 相關 prompt 產生短清單
- **WHEN** 在可解析 project 的 session 送出與某些 knowledge slice 相關的 prompt，且檢索有過 gate 的命中
- **THEN** hook SHALL 注入 ≤k 列短清單，每列含標題、一行摘要、該 slice 的絕對路徑與 `slice_id`，並依 `read_hint` 附 show 或 Read 提示

#### Scenario: hint 模式為 show 時命令已填好歸因參數
- **WHEN** `shortlist.read_hint` 為 `show`（或未設定）
- **THEN** 提示中的 `hippo show --agent` 命令 SHALL 含本 session 的 `--tool` 與 `--session-id`；設為 `read` 時提示 SHALL 與既有 Read 提示相同

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

`hippo search` CLI 入口與 `search()` 函式 SHALL 於查詢進入 SQLite FTS5 前套用 `to_fts_query()` 進行淨化，防止 `word:` 形式之 column-filter 形查詢觸發 SQLite `no such column` 或語法錯誤。

#### Scenario: column-filter 形查詢不致語法錯誤

- **WHEN** 對 `search()` 傳入 `build: f5df394`、`tag:123` 或 `col:` 等 column-filter 形查詢
- **THEN** 查詢 MUST NOT 拋出 `sqlite3.OperationalError` 或 `SearchIndexError`，且回傳 list（可為空）

#### Scenario: sanitize 後 token 仍可命中

- **WHEN** 索引語料含 body 為 `build f5df394 details` 之 slice，且查詢為 `build: f5df394`
- **THEN** 結果 MUST 命中該 slice（sanitize 後 tokens `build`／`f5df394` 以 OR-join 比對，不因淨化而搜不到）

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

### Requirement: 短清單同主題折疊 keep-newest

prompt-retrieval hook 在 `shortlist.collapse_same_topic`（預設 true）開啟時，SHALL 於 claim 前對過取的候選依 `paulsha_hippo/topic.py` 的同主題判定分組：兩筆同組 ⇔ 同 `project`（或同 `projects.yaml` 頂層 `families:` 宣告的 family）且（canonical title 相等 ∨ 任一 `aliases` 相等 ∨ 標題 token Jaccard ≥ 0.6 且 token 數 ≥ 4）；每組只保留 `captured_at` 最新者，其餘 sl_id SHALL 寫入該次 offered ledger 記錄的 `collapsed` 陣列（不進 per-session 去重集合，也不計入 offered 分母）。`search()` 本身 MUST NOT 加入新鮮度排序或改變既有 determinism；`search()` 回傳 SHALL 多帶 `captured_at`。flag 關閉時行為 MUST 與現行一致。沒有 family 設定時，不同 project 的候選 MUST NOT 被視為同主題。

#### Scenario: 同主題三筆只 claim 最新
- **WHEN** 過取的 12 筆候選含 3 筆同 project、canonical title 相同但 `captured_at` 不同的 slice
- **THEN** 注入的短清單 SHALL 只含最新那筆，offered ledger 該筆記錄的 `collapsed` SHALL 含另外 2 個 sl_id，下一個 prompt 若命中同組 SHALL 仍以最新者為準

#### Scenario: 短標題與跨 project 不折疊
- **WHEN** 兩筆標題 token 數 < 4 且僅 Jaccard 相似，或兩筆分屬未設 family 的不同 project
- **THEN** MUST NOT 折疊，兩者皆可被 claim

#### Scenario: flag 關閉回到現行行為
- **WHEN** `config.yaml` 設 `shortlist.collapse_same_topic: false`
- **THEN** hook MUST NOT 折疊，offered 記錄 MUST NOT 含 `collapsed`

