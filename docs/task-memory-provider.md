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
