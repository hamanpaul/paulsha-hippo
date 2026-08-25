## ADDED Requirements

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

## MODIFIED Requirements

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
