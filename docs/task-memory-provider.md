# Task memory provider CLI 契約

`hippo task-memory provide` 與 `hippo task-memory fetch` 是供外部 task adapter 呼叫的可選 subprocess 邊界。Hippo 只處理 JSON；不匯入 Cortex、不寫 Cortex state，也不寫改既有 Hippo offered/read/applied ledger。

## 授權與搜尋範圍

- provider 只接受 schema major `1` 的 Cortex task-memory envelope。
- `delivery.host_scope.allowed_evidence_sources` 必須精確包含 `hippo`，否則回 `permission-denied` 且不呼叫搜尋。
- envelope 的頂層 `project` 必須等於 `delivery.host_scope.repo`。Hippo 將 repo remote 正規化後，必須在既有 `projects.yaml` 與 generated `project-hippo.yaml` union registry 中唯一對應到一個 slug；沒有對應或對應多個 slug 都回 `scope-mismatch`。
- MOC 搜尋固定傳入該 slug、`limit=3`、`include_decayed=false`。provider 不會以 `project=None` 執行全域搜尋。
- 回傳 payload 的 `project` 保持 Cortex canonical repo；內部 Hippo slug 不作為跨程序 project identity。

## Provide

```sh
hippo task-memory provide [--memory-root <root>] [--timeout-seconds <seconds>]
```

stdin 是 Cortex `TaskMemoryContext.to_envelope(mode=...)` 的 UTF-8 JSON；mode 必須是 capability 已啟用的 `note_fetch`、`snapshot` 或 `inline`。stdout 是一個 provider payload JSON，不含 log 或提示文字。省略 `--memory-root` 時採 `HIPPO_MEMORY_ROOT` 與 Hippo 既有預設路徑。

provider 最多回三個候選。`snapshot` manifest entry 帶完整 redacted note body；`note_fetch` manifest 不帶正文；`inline` 回傳最多 800 字元的 redacted excerpt。`content_hash` 對應實際交付 bytes：snapshot/note_fetch 是完整 redacted body，inline 是交付的 excerpt。`content_version` 以原 note bytes 的 SHA-256 標示版本。

manifest schema 為 `hippo/task-memory-manifest/v1`。其 `sha256` 是移除 `sha256` 欄位後的 canonical JSON SHA-256：UTF-8、`ensure_ascii=False`、`sort_keys=True`、`separators=(",", ":")`。

## Fetch

```sh
hippo task-memory fetch [--memory-root <root>] [--timeout-seconds <seconds>]
```

stdin 是下列 wrapper。`envelope` 是原始 Cortex request；`manifest` 必須是同一次 `provide` 回傳的 `delivery.manifest`；`note_id` 是 Cortex 已驗證 manifest 的候選 ID。

```json
{
  "envelope": {"schema_version": "1", "task_id": "task-id", "...": "原始 request 欄位"},
  "manifest": {"schema": "hippo/task-memory-manifest/v1", "...": "provide 回傳的 manifest"},
  "note_id": "slice-id"
}
```

fetch 重新核對來源授權、registry project、task/project、manifest canonical hash、候選 membership 與 content version/hash，再以 note ID 從同一個 project-scoped MOC search 結果定位內容。它不接受路徑參數，也拒絕 manifest entry 含 `path` 等 locator 欄位。note 正文只寫到 stdout 的 JSON `content` 欄位，不寫 log 或 stderr。

## Bounds 與錯誤

- stdin 最多 64 KiB；原 note 最多 128 KiB；redacted note 最多 64 KiB；stdout 最多 512 KiB。
- `--timeout-seconds` 範圍為 0.05–60 秒，預設 10 秒。
- 成功時 exit `0`、stdout 一行 JSON、stderr 空白。失敗時 stdout 空白；stderr 僅輸出 `hippo-task-memory: <code>`，並回傳非零 exit code。

| Exit | Error code | 意義 |
|---:|---|---|
| 10 | `permission-denied` | allowed evidence source 未授權，或讀取被作業系統拒絕 |
| 11 | `timeout` | provider 超過執行期限 |
| 12 | `scope-mismatch` | repo 與 host scope 不符、registry 無唯一對應或路徑越界 |
| 13 | `hash-mismatch` | note bytes 或 content version 與 manifest 不符 |
| 14 | `unsupported-schema` | request schema major 不支援 |
| 15 | `manifest-mismatch` | manifest 無效、注入 locator，或 note 不在本次 manifest |
| 16 | `invalid-request` | JSON/envelope 格式錯誤或 capability/mode 不符 |
| 17 | `size-limit` | stdin、note 或 stdout 超過上限 |
| 18 | `provider-error` | 其他 bounded provider/search/read failure |

沒有候選是成功的空候選 payload，由 Cortex adapter 依既有規則判為 `no-authorized-candidates`。未知 major 則使用 `unsupported-schema`，不可降級成空結果。

## Cortex adapter 接線

1. provider callback 將 request JSON 寫入 `hippo task-memory provide` stdin，只有 exit `0` 時解析 stdout payload。
2. 將 exit `10` 映射成 `PermissionError`、exit `11` 映射成 `TimeoutError`；其餘非零退出維持 provider error，並保留 bounded code 供 diagnostic 對應 `scope-mismatch`、`unsupported-schema` 等 contract failure。
3. 建立 per-task `fetch_note(task_id, note_id)` closure，捕捉原 request 與已驗證 provider payload 的 manifest；核對 task ID 後將 `{envelope, manifest, note_id}` 傳給 `hippo task-memory fetch`。成功時解析 stdout 的 `content`，回傳字串給 Cortex callback；Hippo 與 Cortex 都驗 content hash。
4. Cortex 負責自己的 receipt/sidecar。Hippo provider 不回寫 task KPI，也不改 legacy usage ledger。

## R75 重排序 shadow 與 A/B（#176，決策紀錄 v5 Q1；預設關閉）

`off` 不執行 rerank；`shadow` 只量測、不改正式輸出；`ab` 依 task ID 分組並允許 R75 組改變正式候選。`shadow` 與 `ab` 僅限 public cortex 的 repo 與 project slug，且送出前都需要 deny terms 檔與 `TYPESAFE_API_KEY`。真正打開會把 live public cortex 記憶送往 TypeSafe 的模式，需先核准。

| 環境變數 | 說明 |
|---|---|
| `HIPPO_TASK_MEMORY_RERANK` | `off`（預設）、`shadow` 或 `ab`，大小寫不敏感；其他值都視為 off。off 時不做任何事、零外部呼叫 |
| `HIPPO_TASK_MEMORY_RERANK_DENY_TERMS` | 私有字詞清單檔路徑（一行一個）。缺少或空白時回退，不送出 |
| `TYPESAFE_API_KEY` | TypeSafe key，只從 environment 讀取。缺少時回退，不送出 |
| `HIPPO_TASK_MEMORY_AB_SEED` | A/B 分組 seed；預設固定為 `q2-2026-10` |

**shadow 流程：**
1. 正式 payload 產生並驗證之後，另外做一次 `moc.search(limit=12)`。
2. 每則候選取標題＋內文前 800 字元，用內建規則＋私有字詞清單過濾；task intent 也要過濾。
3. 以 R75-slot-v1（`h2_bench.rerank_r75`）重排：可送出的候選各送一個單題 Noul，12 路並行、逾時 5 s、不重試；不可送出的候選位置不動；任何錯誤都整題回退。
4. 寫一筆 receipt 到 `<memory_root>/runtime/experiments/task-memory-rerank-shadow.jsonl`。

**receipt**（schema `hippo/task-memory-rerank-shadow/v1`；不存記憶內容與原始 intent）：
- `moc_search_revision`：Hippo 版本與索引檔的 size／mtime；
- `query_sha256`、`production_manifest_sha256`；
- `production_a_top3` 與 `shadow_r75_top3`（note ID）；
- `candidates`：top-12 的 note ID、content digest、egress 與原因、noul；
- `fallback`、`wall_ms`、`cost_usd`、`jev_model`，以及私有字詞清單的筆數與 sha256。

shadow 的任何失敗都只記在 receipt，不影響正式輸出，也不改變錯誤碼。

**A/B 流程：** 只對 `github.com/hamanpaul/paulsha-cortex` 的允許 repo/project slug 生效。分組計算
`sha256(seed + ":" + task_id)`，摘要第一個 byte 為偶數時分到 `R75`，否則分到 `A`；預設 seed 為
`q2-2026-10`。同一個 task ID（包含同一 work item 的多張卡）在相同 seed 下固定同組。

- **A 組：** 正式 payload 與 off 完全相同，包含 candidates、manifest entries 與 manifest SHA-256；CLI 在正式 JSON
  寫出後仍以 deferred job 背景執行 shadow。receipt 記 `arm: "A"`、`applied_arm: "A"`。
- **R75 組：** 正式 payload 建立前先搜尋 top-12，依 deny 規則過濾，再執行 R75-slot-v1。成功且結果非空時，以選出的
  hits 建立正式 notes、content hash/version 與 manifest。若 deny terms/key 缺少、搜尋失敗、JEV 錯誤或逾時、task
  被 deny、結果為空，或 hit 超出 project scope，正式輸出回退為 A top-3，並將原因寫進 `fallback`；回退時
  `applied_arm` 為 `"A"`，成功時為 `"R75"`。R75 是同步路徑，受 `--timeout-seconds` 的 provider 總 deadline 約束。
- 不在允許範圍的 task 不會搜尋 top-12 或呼叫 JEV，正式輸出保持 A，receipt 記 `fallback: "out-of-scope"`。
- 缺 deny terms 或 API key 也不會搜尋／送出 R75 候選；A 組仍照常排入背景 shadow，R75 組回退 A。

**A/B receipt** 沿用 `hippo/task-memory-rerank-shadow/v1` 與既有 receipt 檔，並包含：
`arm`、`applied_arm`、`production_a_top3`、`shadow_r75_top3`、`delivery_manifest_sha256`、`wall_ms`、
`cost_usd` 與 `fallback`。`delivery_manifest_sha256` 是實際交付 payload 的 manifest hash；receipt 不保存 note
正文或原始 intent。

**CLI 的 shadow 不同步執行：** `hippo task-memory provide` 會先寫出並 flush 正式 JSON，再把最小 job（只含 task ID、project、intent、正式 top-3 的 note ID 與 manifest hash，不含記憶內容）寫進 `<memory_root>/runtime/experiments/task-memory-rerank-pending/` 的私有暫存檔（0600），以 `start_new_session` 起一個脫離的背景行程（`python -m paulsha_hippo.task_memory_rerank --job <檔>`）計算並寫 receipt；背景行程讀完即刪除 job 檔。Hippo 主行程照常結束，不經 pipe 傳資料。此流程適用於 `shadow` 模式與 A/B 的 A 組；R75 組在正式 payload 建立前同步計算。
- A 組 shadow 不會吃掉 provider 的 deadline 或 Cortex 的 subprocess timeout（兩者預設都是 10 s），正式 pull 的延遲幾乎不變；shadow 本身花的時間記在 receipt 的 `wall_ms`。R75 組則同步執行，必須在 provider deadline 內完成。
- 背景行程啟動失敗也不影響 exit code。
- 直接以函式庫呼叫 `TaskMemoryProvider.provide()`（`defer_shadow=False`）時，shadow 會在回傳前同步執行。
