---
status: accepted
work_item: issue-117-project-buckets
---

# worktree／主 repo project bucket 收斂 tasks

## Task 1: resolver worktree 收斂與暫存 checkout

- [x] 先寫測試：主 root、`<repo>-worktrees/<branch>`、`.worktrees/<name>` 三種 cwd 在「只有 roots」「roots＋remotes」「未登記有 remote」「未登記無 remote」四種 registry 狀態下解析出同一 slug（RED：sibling worktree 回 raw remote／worktree 目錄名）。
- [x] 先寫測試：暫存根下無 git／無 remote 的 checkout、已刪 cwd、暫存根本身歸 `_unknown`；有 remote 或已登記 root 者照常解析；非暫存目錄維持目錄名 fallback。
- [x] 實作：linked worktree 歸併主 repo root 後比對 roots、目錄名 fallback 以主 repo root 命名；`ephemeral_roots()`／`is_ephemeral_path()`（`HIPPO_EPHEMERAL_ROOTS` 覆寫，排除 `/`、HOME 與其祖先）。
- [x] 測試環境隔離：`GIT_CEILING_DIRECTORIES` 與清空暫存根，repo checkout 在其他 repo 內或暫存目錄下時不產生假失敗。

## Task 2: 註冊時補登 remotes

- [x] 先寫測試：registry 只有 roots 的 project，主 repo 與 sibling worktree session 皆補上探測到的 remote，重跑不變更（RED）。
- [x] 先寫測試（安全邊界）：remote 已由他 slug 認領、registered root 只是祖先目錄、payload-only remote 皆不補登。
- [x] 實作：`importer/remote_backfill.root_registered_remote()` 接入 `_discovery_candidate`。

## Task 3: 既有 registry 一次性 backfill

- [x] 先寫測試：dry-run 不寫檔；`--apply` 寫入、寫前備份、回復指令可還原原 bytes、重跑冪等；legacy-only project 補進 generated registry 且 legacy 檔不改；conflict／root-missing／not-a-repo／not-repo-root／no-remote 只回報不寫入；補登後 sibling worktree 經 remote 收斂。
- [x] 實作：`hippo registry backfill-remotes`；`registry.record_discoveries()` 於同一 lock 內批次合併並在 replace 前備份。

## Task 4: bucket 合併 dry-run impact report

- [x] 先寫測試：零寫入；raw remote bucket 併入補登後 slug 並附 `hippo knowledge rekey --dry-run` 指令；registered／未登記 raw remote bucket 不動；暫存 sandbox bucket 列為 unresolved；`_unknown` 成因分類與可回收比例；`--no-backfill-overlay`。
- [x] 實作：`paulsha_hippo/bucket_report.py`＋`hippo knowledge bucket-report`。

## Task 5: 文件與收尾

- [x] `docs/project-registry-contract.md`、README、changelog 碎片與 `CHANGELOG.md [Unreleased]` 同步。
- [ ] 部署後：真實 registry 執行 `backfill-remotes --apply`，以實際 ledger 驗證 worktree session 的 offered 來自主線 bucket（部署步驟，非本 PR 範圍）。
- [ ] P2：依 impact report 決定是否以 `hippo knowledge rekey` 合併既有 bucket（需另行授權）。
