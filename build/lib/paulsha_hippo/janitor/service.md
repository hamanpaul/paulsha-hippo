# Stage 2 Janitor Service

## 1. 角色

janitor 是 `paulsha-memory` 的常駐治理服務，負責在 pipeline 之外執行記憶維護：

- 掃描 `decayed/reactivation` 候選
- 產生 replay bundle
- 對 knowledge 層做降權 / 重新啟用判斷
- 輸出可追溯的 ledger 事件與證據索引

## 2. 服務流程

1. 讀取 `inbox/`、`work-centric/`、ledger 事件。
2. 找出長期未引用、來源失效或與新資料衝突的條目。
3. 寫入 `decayed` 事件，並把條目移出高信任檢索集合。
4. 掃描新的引用、人工確認、或 replay 支持的候選。
5. 針對符合條件的條目寫入 `reactivation` 事件。
6. 產生 replay / evidence index，供 sync-back gate 與 handoff 使用。

## 3. 排程建議

以 `systemd` 為預設服務化機制，最小排程分成兩層：

| 單元 | 週期 | 用途 |
|---|---|---|
| `paulsha-memory-ingest.timer` | 每 15 分鐘 | 吸收新 inbox artifact，避免 backlog 堆積 |
| `paulsha-memory-janitor.timer` | 每日 02:30 | 執行 decayed/reactivation 掃描、產生 replay bundle 與證據索引 |

## 4. Guardrails

1. janitor 不直接修改 raw artifact。
2. janitor 不自行定義 Stage 3 frontmatter schema。
3. 任何 `reactivation` 都必須能追到證據來源與 replay context。
4. sync-back 前必須保留最近一次 janitor 掃描的測試證據。
