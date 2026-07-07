# Stage 2 Scope

- 主題：`paulsha-memory`（來源 `obs-auto-moc`）
- 目標：把 agent 記憶收斂成可治理、可回放、可衰退的 Stage 2 中樞
- 來源基準：
  - `docs/research/02.obs-auto-moc-memory-dream-mode-24-7-service-notes-.md`
  - `docs/research/05.paulshaclaw-overview-architecture-stages-dependencies-acceptance.md`

## 1. 角色與邊界

| 角色/元件 | Stage 2 內責任 | 不屬於 Stage 2 |
|---|---|---|
| `paulsha-memory` | intake/importer、classifier、replay bundle、`atomized_from` provenance、`record-agent-reference`、`decayed/reactivation` ledger 事件 | 直接 atomize 執行、security approval、persona contract |
| PicoClaw on pi3 runtime | 執行 atomize、裝置在地 runtime 決策、把原子化結果送回 Stage 2 | canonical memory 治理、janitor 決策 |
| `ops-companion` | 無；僅作 Stage 6 安全治理與 approval gate | Stage 2 runtime、memory routing |

## 2. 記憶路由規則

Stage 2 的 canonical path 固定為 `inbox -> work-centric -> knowledge`，不得反向繞過治理層：

1. `inbox`
   - 接收 session distilled outputs、plan、research、report、attachment metadata。
   - 保留原始來源與 ingestion metadata，但不直接作為長期知識查詢入口。
2. `work-centric`
   - 依 project / workstream / story 聚合正在進行的上下文。
   - classifier 在此層做去重、關聯補強、replay candidate 準備。
3. `knowledge`
   - 只收斂可重用、可引用、可回放的結論。
   - 寫入前必須已具 provenance 與來源引用。

## 3. 事件治理

`paulsha-memory` 必須把 `decayed/reactivation` 視為 Stage 2 一級事件，而不是隱含狀態：

- `decayed`
  - 條件：事實過期、來源失效、長期未被引用或與新證據衝突。
  - 動作：保留原引用，標註降權原因，從高信任檢索集合移出。
- `reactivation`
  - 條件：新證據、人工確認、或 replay 驗證重新支持既有條目。
  - 動作：補記 `record-agent-reference` 與 replay context，恢復可檢索權重。

## 4. janitor / replay 邊界

- janitor 是獨立 service，不是 pipeline 尾端的順手步驟。
- replay bundle 只讀取 distilled artifact 與 ledger 事件，不直接掃描 raw prompt。
- importer / classifier / replay 必須可獨立驗證，作為 sync-back 前的 Stage 2 gate。

## 5. sync-back gate

`paulsha-memory` 最終落點為 `custom-skills/paulsha-memory`，但回寫前必須同時滿足：

1. Stage 2 importer / classifier / replay 驗證通過。
2. `decayed/reactivation` 事件規則已寫入規格並有測試證據。
3. 證據保留於 `docs/superpowers/workstreams/stage2-paulsha-memory/evidence/`。
4. 不在 Stage 2 自行擴充 Stage 3 frontmatter schema（必填欄位 `slice_id / artifact_kind / supersedes / checksum` 由 Stage 3 擁有）。
