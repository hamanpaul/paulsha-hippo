---
status: accepted
work_item: issue-117-project-buckets
---

## Why

同一 repo 的主 checkout 與 linked worktree 被解析成不同 project：registry 只登記 roots、沒有 remotes 時，`<repo>-worktrees/<branch>` 不在 root 前綴下，落到 remote fallback 回傳 raw remote 字串，knowledge bucket 因此碎裂（主線 bucket 與 raw remote bucket 各自累積，worktree 內的 session 撈到另一套記憶）。另外暫存目錄下的 sandbox checkout（無 git 或無 remote）以目錄名產生 `checkout` 這類假 project。#148 將核心修正分級為 P1；既有 note 的大範圍合併為 P2，只做可回復的 dry-run 與 impact report。

## What Changes

- `resolve_project`：linked worktree 先歸併為主 repo root 再比對 roots；無 remote 的目錄名 fallback 以主 repo root 命名；暫存根下無 remote、未登記的 checkout 歸 `_unknown`（`HIPPO_EPHEMERAL_ROOTS` 可覆寫暫存根）。
- importer discovery：slug 由恰等於主 repo root 的 registered root 派生時，以現場探測的 origin remote 補登 `remotes`（祖先 root、多 slug 共登、remote 已被他 slug 認領時不補）。
- 新增 `hippo registry backfill-remotes`：既有 registry 一次性補 remotes，預設 dry-run，`--apply` 寫前備份並輸出回復指令，冪等。
- 新增 `hippo knowledge bucket-report`：唯讀 impact report，列出 bucket 合併去向、筆數、可執行的 `hippo knowledge rekey` 指令與 `_unknown` 成因分類；不搬檔。

## Capabilities

### Modified Capabilities

- `stage2-memory-readback`：Hybrid project resolution 加入 worktree 收斂與暫存 checkout 規則；新增 registry remotes 補登與 bucket 合併 impact report 需求。
