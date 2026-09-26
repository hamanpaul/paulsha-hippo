---
type: fix
---
- 修 issue #117：worktree 與主 repo 解析成不同 project、knowledge bucket 碎裂。根因是 registry 只登記 roots、沒有 remotes 時，`<repo>-worktrees/<branch>` 不在 root 前綴下，落到 remote fallback 回傳 raw remote 字串另開 bucket；而舊 discovery gate 認定 root 派生的 slug「非 remote 派生」，永遠補不到 remotes。
  - `resolve_project`：linked worktree 先歸併為主 repo root 再比對 roots，無 remote 的目錄名 fallback 也以主 repo root 命名——主 root、`<repo>-worktrees/<branch>`、`.worktrees/<name>` 三種 cwd 收斂到同一 slug。暫存根（預設 `/tmp`、`/var/tmp`、`/dev/shm` 與系統暫存目錄；`HIPPO_EPHEMERAL_ROOTS` 以 os.pathsep 分隔覆寫、空字串停用；`/`、HOME 與其祖先一律排除）下無 remote、未登記的 checkout 歸 `_unknown`，不再以目錄名產生 `checkout` 這類假 project。
  - importer discovery：slug 由恰等於主 repo root 的 registered root 派生時，以現場探測的 origin remote 補登 `remotes`；registered root 只是祖先目錄、同一 root 多 slug 登記、remote 已由其他 slug 認領、或 remote 只來自 payload 時一律不補。
  - 新增 `hippo registry backfill-remotes`：既有 registry 的 remotes 一次性補登，預設 dry-run，`--apply` 於 registry lock 內寫前備份（`project-hippo.yaml.bak-117-<UTC 時戳>`）並輸出回復指令，冪等；legacy `projects.yaml` 只讀。`registry.record_discoveries()` 支援同一 lock 內批次合併。
  - 新增 `hippo knowledge bucket-report`：唯讀 impact report，以補登後的 union registry 重新推導各 knowledge note 的目標 slug，列出 bucket 合併去向與筆數、可執行的 `hippo knowledge rekey --dry-run` 指令與 `_unknown` 成因分類（atomizer-coerced／provenance-only／ephemeral-cwd／no-remote-evidence／no-cwd／payload-missing）；既有 note 的實際合併屬 P2（#148），本版不搬檔。
  - 測試隔離：`test_project_resolver` 以 `GIT_CEILING_DIRECTORIES` 與清空暫存根隔離 checkout 位置，repo checkout 在其他 repo 內（`.worktrees/*`、`.claude/worktrees/*`）或暫存目錄下時不再出現環境造成的假失敗；`tests/conftest.py` 預設清空暫存根。
