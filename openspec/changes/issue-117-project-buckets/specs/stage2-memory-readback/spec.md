## MODIFIED Requirements

### Requirement: Hybrid project resolution

`resolve_project` SHALL 依下列優先序由 session 工作目錄決定 project slug：(1) cwd 或顯式 git toplevel 命中 configured project root（最長前綴）時回傳該 slug；(2) 顯式 payload remote 命中 configured `remotes` 時回傳該 slug；(3) 工作目錄位於 linked worktree 時，SHALL 先歸併為主 repo root（`git rev-parse --git-common-dir`）再比對 roots；(4) 位於有 `origin` remote 的 git repository 內時，remote 命中 configured `remotes` 則回傳該 slug，否則回傳正規化的 `owner/repo` 形；(5) 位於無 remote 的 git repository 內時，回傳**主 repo root** 的目錄名；(6) 不在 repository 內時回傳工作目錄名。步驟 (5)(6) 的路徑位於暫存根（預設系統暫存目錄，`HIPPO_EPHEMERAL_ROOTS` 可覆寫）之下時，SHALL 回傳 `_unknown`，MUST NOT 以目錄名產生 project。父目錄含兩個以上 git repository 時，slug SHALL 為以父目錄名為前綴的 tree path。Resolution SHALL 同時以偵測到的 git remote 填入 provenance。Git 偵測 SHALL 為 best-effort，git 資訊不可得時 MUST 降級而不 raise。

#### Scenario: Repo with a remote resolves to owner/repo
- **WHEN** 工作目錄位於有 `origin` remote、且未登記的 git repository 內
- **THEN** project slug 為正規化的 `owner/repo` 形，provenance 記錄該 remote

#### Scenario: Directory without a repository resolves to the working-folder name
- **WHEN** 工作目錄不在任何 git repository 內、也不在暫存根之下
- **THEN** project slug 為工作目錄名

#### Scenario: Multi-repo workspace resolves to a tree path
- **WHEN** 工作目錄所屬 repo 的父目錄含兩個以上 git repository
- **THEN** project slug 為以父目錄名為前綴的 tree path

#### Scenario: Git unavailable degrades instead of failing
- **WHEN** 無法取得工作目錄的 git 資訊
- **THEN** resolution 降級為工作目錄名（暫存根之下則為 `_unknown`），且不 raise

#### Scenario: Worktree conventions converge to the main repo slug
- **WHEN** 同一 repo 的主 root、`<repo>-worktrees/<branch>`、`<repo>/.worktrees/<name>` 分別作為工作目錄，且 registry 只登記主 repo root（尚無 remotes）
- **THEN** 三者 SHALL 解析出同一個 registered slug，sibling worktree MUST NOT 回傳 raw remote 形

#### Scenario: Remoteless worktree converges to the main repo directory name
- **WHEN** 無 remote、未登記的 repo 以 linked worktree 作為工作目錄
- **THEN** slug SHALL 等於主 repo root 的解析結果，MUST NOT 使用 worktree 目錄名

#### Scenario: Ephemeral checkout without remote is unresolved
- **WHEN** 工作目錄位於暫存根之下（如 planning sandbox 的 `checkout` 複本），無 remote 且未命中任何登記 root
- **THEN** project slug SHALL 為 `_unknown`

## ADDED Requirements

### Requirement: Registry remotes backfill and bucket merge impact report

Importer discovery SHALL 在 slug 由「恰等於主 repo root 的 registered root」派生時，以現場 git 探測的 origin remote 補登該 slug 的 `remotes`；registered root 僅為祖先目錄、同一 root 由多個 slug 登記、或 remote 已由其他 slug 認領時 MUST NOT 補登，payload 夾帶的 remote MUST NOT 經此路徑落盤。Hippo SHALL 提供 `hippo registry backfill-remotes`：預設 dry-run 且不寫檔，`--apply` 才寫入 generated registry，寫入前 SHALL 在 registry lock 內備份原檔並輸出回復指令，重跑 SHALL 冪等且不產生重複 remotes；legacy `projects.yaml` MUST NOT 被改寫。Hippo SHALL 提供唯讀的 `hippo knowledge bucket-report`，列出各 knowledge bucket 的合併去向與筆數、可執行的 rekey 指令與 `_unknown` 成因分類，MUST NOT 搬移或寫入任何檔案。

#### Scenario: Registration backfills remotes for a roots-only project
- **WHEN** registry 只登記某 project 的主 repo root，且 auto-write 開啟時該 repo 或其 sibling worktree 有 session 被 ingest
- **THEN** registry SHALL 為該 slug 補上探測到的 origin remote，重跑不再變更

#### Scenario: Backfill is dry-run by default, reversible, and idempotent
- **WHEN** 對只登記 roots 的 registry 執行 `backfill-remotes`，之後以 `--apply` 執行兩次
- **THEN** dry-run 不寫檔；第一次 apply 寫入 remotes、產生備份與回復指令；第二次 apply 回報已存在、不寫檔也不產生新備份

#### Scenario: Unsafe backfill candidates are reported, not written
- **WHEN** 某 root 探測到的 remote 已由其他 slug 認領，或 root 不存在、不是 repo toplevel、沒有 remote
- **THEN** backfill SHALL 以 conflict／root-missing／not-a-repo／not-repo-root／no-remote 回報，且不寫入 registry

#### Scenario: Bucket report is read-only and classifies unknown causes
- **WHEN** 執行 `hippo knowledge bucket-report`
- **THEN** 報告 SHALL 列出 raw remote bucket 併入補登後 slug 的筆數與 rekey dry-run 指令、`_unknown` note 的成因分類與可回收比例，且 memory root 與 registry 的檔案內容 MUST 維持不變
