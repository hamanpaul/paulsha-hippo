# 全 open issue 清零：master spec（2026-07-10）

> 解決 repo 全部 9 個 open issues（#9 #10 #14 #15 #16 #17 #18 #19 #20）的單一設計文件。
> 實作由 ultracode workflow 編排：6 個 code PR（相依拓撲 pipeline、驗收後自動 merge）→ runtime 恢復序列 → 收口批次。
> 根因分析來源：Codex（gpt-5.6-sol, xhigh）2026-07-10 實地唯讀調查，關鍵結論已逐項對照程式碼。

## 1. 現況關鍵判斷（分析基線）

- **#15 直接故障**：systemd dream 環境找不到裸命令 `claude`（NVM PATH 不在 unit 環境），互動 shell 正常。atomizer config 存的是裸命令；service unit 只修了 Python interpreter（`paulsha_hippo/ops.py:166`）。
- **#15 結構故障**：狀態機無 terminal failure state（`paulsha_hippo/ledger/processing.py:14` 合法狀態僅 `split/promoted/skipped`）；retry 超限後刻意保留毒快取（`paulsha_hippo/atomizer/pipeline.py:128`）、session 停留 `split`（`paulsha_hippo/atomizer/pipeline.py:462`）；既有測試把毒快取保留鎖成預期行為。
- **#16 根因修正**：issue 內「reindex 只掃 manifest」推測不成立——`build_index()` 本就全掃 `knowledge/**/*.md`（`paulsha_hippo/moc/search.py:52,111`）。真因是 `slugify()` 未限制 UTF-8 byte 長度（`paulsha_hippo/moc/naming.py:17,37`），超長 LLM title 組出超過 NAME_MAX 的檔名，MOC reconcile rename 階段 `ENAMETOOLONG` 使 `run_moc()` 中止（`paulsha_hippo/moc/runner.py:9`），reindex 根本沒執行。
- **#18 根因修正**：`matched/cited` 已在現行程式標為 deprecated（`paulsha_hippo/usage.py:1`）、SessionEnd recorder 已 unwired（`paulsha_hippo/usage_ledger.py:1`）、主訊號已改為 Claude PostToolUse(Read)（`paulsha_hippo/hooks/claude_post_tool_use.py:85`）。「修到非零」是錯誤方向。
- **#17 定性**：是已知設計缺口，非 ledger 漏記。三家 CLI 都有 SessionStart（僅注入 orientation，`paulsha_hippo/hooks/_wakeup_common.py:143`）；task-relevant shortlist 只掛 Claude `UserPromptSubmit`（`paulsha_hippo/hooks/claude_user_prompt_submit.py:20`）；共用 shortlist 函式本身可跨 tool（`paulsha_hippo/hooks/_shortlist_common.py:112`）。
- **#19 定性**：`.lock` 是 flock rendezvous inode，不代表仍被鎖；問題是 per-session lock namespace 無界成長（`paulsha_hippo/importer/pipeline.py:356`），且執行中直接 unlink 會破壞互斥。`dream supervise` 無 single-instance guard（`paulsha_hippo/ops.py:195`）。orchestrator 只存 exception 類別、丟失 errno（`paulsha_hippo/dream/orchestrator.py:25`）。
- **runtime 快照已漂移**：#20 的 187/440、397 locks 等數字與當前 runtime 不符（中間曾重建）。**所有驗收一律以執行時實測為準，不得寫死歷史數字。**
- **本機 CLI 可用性（2026-07-10 實測）**：`claude`、`codex`、`copilot`、`gemini` 可用；`antigravity` 不存在。

## 2. 目標／非目標／邊界

**目標**
1. 全部 9 個 open issues 達到可關單狀態（#9 除 reboot 驗收外全備妥）。
2. 原子化管線恢復生產：單一 dream writer、失敗顯性、毒快取有淘汰機制。
3. 檢索索引達成不變量 `indexed IDs == eligible IDs`。
4. 跨 CLI 消費：三家 CLI 依實測能力接線或誠實標示。

**非目標**
- 不 bump `VERSION`（release 時機由使用者另行決定）。
- 不做 PyPI 發佈（若需要另開 issue，不阻擋 #9）。
- 不自動執行真機 reboot（#9 冷啟驗收留人工）。
- antigravity preset 不實作（標 unavailable，命令契約確認後另補）。

**邊界／已拍板決策**
| 決策點 | 拍板 |
|---|---|
| #18 處置 | retitle 為「跨 CLI offered → read → applied funnel」，與 #17 同批實作 |
| runtime 恢復操作 | workflow 自動執行，備份先行、可回滾 |
| spec 組織 | 單一 master spec（本檔），plan 按 PR 批次拆 |
| 執行架構 | 相依拓撲 pipeline（無全域 barrier） |
| merge 策略 | 每批驗收通過後 workflow 自動 `gh pr merge` |

## 3. PR 批次規格

### 3.1 PR-A `feature/15-atomize-failure-chain`（P0 失敗鏈）— Closes #15

**範圍**：#15 全部 + #19 的 singleton/錯誤可見性 + #10 的最小 service 修復。三者是同一條失敗鏈。

**行為變更**
1. **失敗分類器**（atomizer promote 路徑）：
   - `backend unavailable`（executable 不存在／config 無效）→ 不重試，session 立即進 `parked`。
   - `transient`（timeout／網路／non-zero exit）→ 有限次退避重試（上限沿用現有 retry budget 設定），超限 `parked`。
   - `invalid output`（非 JSON／schema 不符）→ **先淘汰該 session 的 LLM 快取**再重試，有限次，超限 `parked`。
2. **狀態機**：`paulsha_hippo/ledger/processing.py` 合法狀態擴為 `split/promoted/skipped/parked`。`parked` 記錄：failure category、attempts、cache key、bounded error message（截斷、去敏）。`parked` session 不再佔用每輪 atomize 預算。
3. **毒快取淘汰**：進 `parked` 時刪除對應 LLM output cache 與 retry sidecar；失敗證據（最後一次原始輸出摘要）落 `runtime/queue/_failed/`。
4. **dream singleton**：`dream run` 入口取得 global nonblocking flock，整輪持有；取不到鎖 → 記 log 後 skip（exit 0），不得競寫。
5. **錯誤可見性**：`paulsha_hippo/dream/orchestrator.py` 保存 bounded 錯誤訊息與 errno（不再只存 exception 類別名）。
6. **backend 絕對路徑**：`hippo init` 產生 atomizer config 時將 backend argv[0] 解析為絕對路徑；`hippo doctor` 以 service-effective 環境（非互動 shell PATH）驗證 backend 可執行。
7. **既有設定遷移**：新增冪等 migration（`hippo doctor --fix-backend` 或等效）：偵測既有 atomizer config 中不可在 service-effective 環境解析的裸命令 → 備份原檔 → 改寫為絕對路徑。恢復序列（§4）把「migration 已執行且 probe 通過」列為前置條件——**只修新設定不足以救既有部署**。
8. **parked 恢復路徑**：`parked` 不是死路。新增 `hippo requeue <session-id>|--all-parked`（`parked → split`，記錄 requeue 事件與原 failure category）；進 `parked` 時保留重試所需 fragments（只刪 LLM output cache 與 retry sidecar，不刪 split 產物）。backend 修復後可 requeue 重走 promote。
9. **測試反轉**：把「毒快取保留」既有測試改為「超限即淘汰」。

**E2E 必測**：backend 故障 → park（含證據）→ 修復 backend → requeue → 成功 promote 的完整循環。

**不做**：lock sharding（PR-C）、preset registry 全量（PR-D）。

**驗收**
- 新增／修改單元測試全過；全套 pytest 過。
- 模擬 non-JSON backend 的 session 在 retry 超限後：狀態 `parked`、快取已刪、`_failed/` 有證據、下輪不再重試。
- 兩個並發 `dream run` 只有一個實際執行。

### 3.2 PR-B `feature/16-index-rebuild`（索引可靠性）— Closes #16

**行為變更**
1. `slugify()` 以 UTF-8 **bytes** 上限截斷（落在 code-point 邊界），保證組合檔名 `<slug>--<slice_id>.md` 總長不超過 NAME_MAX（255 bytes），`--<slice_id>.md` 尾段永不截斷。
2. naming/linker 對單一壞 slice fail-soft：記 warning、跳過該 slice，不中止整輪 MOC。
3. `build_index()` 改為 temp DB 完整建好後 atomic replace；移除「先 unlink 現有 DB」行為（`paulsha_hippo/moc/search.py:54`）——建索引失敗時舊 DB 完整保留。
4. coverage 報表六欄：`scanned / invalid frontmatter / pool-excluded(by reason) / noise-excluded(by reason) / eligible / indexed`。
5. 強不變量：`indexed IDs == eligible IDs`，以動態計算驗證（不寫死數字）。

**驗收**
- 構造超長 title fixture：檔名被 byte-bound、rename 成功、無 `ENAMETOOLONG`。
- 構造單一壞 slice：該 slice 被跳過並記 warning，其餘 slices 正常索引。
- 建索引中途注入失敗：舊 DB 未損毀。
- **三方對賬（防同源自證）**：以獨立 filesystem census（純檔案枚舉，與 index/coverage 掃描邏輯分離實作）建立磁碟 ID 全集；驗證每個 ID 恰有唯一去向（invalid／excluded(reason)／eligible 為互斥完備分割）；indexed ID 集自完成後的 DB 反查。三方一致才算過——不得讓 coverage 與 index 共用同一次掃描迴圈自證。
- 實際 runtime 重建後 `indexed == eligible`（以三方對賬版定義）。

### 3.3 PR-E `feature/14-project-registry`（零相依）— Closes #14

**行為變更**
1. 新增 generated 檔 `project-hippo.yaml`（寫入位置沿用 `paulsha_hippo/paths.py` 的 config 根約定，與手寫 `project-cortex.yaml` 同層）：hippo 從 importer 已解析的 cwd/repo/remote 產出結構化 discovery record。
2. Schema：`projects: [{slug, roots: [], remotes: [], aliases: []}]`；remote URL 正規化（去 credential、統一 scheme）、roots/remotes 去重排序，輸出 deterministic。
3. **分權**：generated 檔不允許手改（檔頭註明）；使用者 override 一律放 cortex/manual 檔。cortex 讀兩份 union 去重（cortex 側行為不在本 repo 範圍，只保證檔案契約）。
4. Opt-in：`project_registry.auto_write: true` 才寫檔（預設 off）。
5. 寫入：temp file + atomic replace + 固定名 lock；stdlib-only、零新依賴。
6. 過渡：讀取端 union-read legacy `projects.yaml` 與新檔，不破壞性搬移。

7. **契約版本化**：完整 path／schema／merge 語義寫入 `docs/`（含 `schema_version` 欄位）；producer contract test 以固定 fixture 驗證輸出逐 byte 符合契約文件。

**驗收**：crash recovery（寫一半殺進程 → 舊檔完好）、重複 discovery 冪等、worktree 路徑歸併到主 repo root、多 remote 正規化去重、producer contract test 過。

**跨 repo 邊界**：cortex 側 consumer 相容（union-read、手改資料遷移）不在本 repo——收口批次開 paulshaclaw 對應 issue 並互相連結；#14 關單以 producer 契約 + 版本化文件為準。

### 3.4 PR-C `feature/19-lock-sharding`（等 PR-A）— Closes #19

**行為變更**
1. importer per-session lock 改為固定 64 個 hash-sharded locks（`lock_<00..3f>.lock`）；碰撞只降低並行度，不影響正確性。
2. legacy per-session locks 一次性清理：僅在確認無舊版 importer 進程執行時（恢復序列的維護窗口內）執行；新版程式不再產生舊命名 lock。
3. `hippo doctor` 新增 runtime 進程健康報告：列出 dream/supervise 進程的 PID、start time、cmdline 路徑，標記非 canonical 路徑（如暫存 worktree）的實例；**只報告，不自動 kill**。

**驗收**：並發 importer 壓力測試互斥正確；locks 目錄檔案數恆為常數；doctor 能識別偽造的孤兒進程 fixture。

### 3.5 PR-D `feature/10-backend-matrix`（等 PR-C）— 條件式 Closes #10

**行為變更**
1. **Declarative preset registry**（取代 `paulsha_hippo/ops.py:20` 硬編碼）：每個 preset 宣告 `name / argv template / required executable / doctor probe / capabilities`。
2. 新 presets：`codex-headless`（`codex exec`）、`copilot-headless`（`copilot -p` 系）、`gemini-headless`；全部是 `custom-argv` 機制的 preset 包裝，機制零新增。antigravity：registry 中標 `unavailable`（命令契約未確認），選單顯示但不可選。
3. `hippo init --backend` 選單化，寫入時 argv[0] 絕對路徑化（沿用 PR-A 機制）。
4. `hippo doctor` 按 preset 的 probe 驗證（service-effective 環境）。
5. **真蒸餾 smoke**：同一 fixture session 對每個本機可用 preset 跑一輪真蒸餾，斷言五種輸出情境的處理：純 JSON／散文包 JSON／截斷輸出／non-zero exit／timeout（後四種以 mock backend 注入，真蒸餾只驗 happy path 一輪）。
6. `openai-compatible` 真端點 smoke 放 integration profile（環境變數 gate，未設 credential 時 skip，不進一般 CI）。
7. **supervise E2E**：無 systemd 情境的 `dream supervise` 前台實測一輪（#10 原始 checklist 項）。

**驗收**：四個本機 preset 真蒸餾 smoke 全過並留存證據（backend、輸出 slice 數）；mock 情境測試全過；doctor 對每個 preset 給出正確可用性判定；supervise E2E 過。

**關單條件（防過早 Closes）**：PR-D 帶 `Closes #10` 僅當「四 preset smoke + supervise E2E + openai-compatible 真端點」全部有實證。任一缺（如無可用真端點 credential）→ PR body 改 `Refs #10` + `policy-exempt:issue-link` label；收口批次把殘項拆成新 issue 後才關 #10。

### 3.6 PR-F `feature/17-cross-cli-consumption`（等 PR-E）— Closes #17，並完成 #18（retitle 後）

**行為變更**
1. **Capability matrix 實查**（進 docs）：逐家確認 codex/copilot 的 prompt-time hook、session-start hook、read/tool attribution 能力；以官方文件＋本機實測為據。
2. **Consumer API**：新增 `hippo recall --cwd <path> --prompt <text> --tool <name> --session-id <id>`，回傳 task-relevant shortlist（重用 `_shortlist_common.build_shortlist_and_record()`），並記 `offered`（含 tool attribution）。
3. **Adapters**：有 prompt-time hook 的平台直接接線；沒有的平台在其 session-start 注入「顯式 recall 指引」（教 agent 呼叫 `hippo recall`），並在 capability matrix 標 `produce-only`（若連 recall 都不可行）。**不假裝 SessionStart orientation 等同 task retrieval。**
4. **#18 funnel**：主漏斗定為 `offered → read`（per-tool 分列）；新增顯式 `applied` 訊號介面（agent structured acknowledgement），無訊號時 funnel 該欄顯示 `n/a`——不做內容 substring 猜測。
5. **漂移修正**：usage CLI 實際同時讀 `offered.jsonl` 與 `memory_usage.jsonl`（`paulsha_hippo/cli.py:578`），文件宣稱單一 ledger——以實作為準修文件。
6. usage 報表按 tool 分列 `offered / read / applied`。
7. **#18 retitle 時序**：開 PR 前先 `gh issue edit 18` 改題為「跨 CLI offered → read → applied funnel」並留言記錄根因修正（matched/cited 已 deprecated）；PR body 帶 `Closes #17` 與 `Closes #18`，merge 時兩單自動關。

**驗收**：codex/copilot session fixture 經 adapter 或 recall 路徑能取得 shortlist 且 `offered.jsonl` 記錄正確 tool；usage 報表 per-tool 分列；capability matrix 有實測證據。

**#18 關單條件（防 n/a 通關）**：
- 必須有至少一條**真實 adapter E2E** 證據：可綁定 session／turn／offered slice 的 `offered → read` 記錄，且附 negative control（無關 prompt 不觸發 offer）——手動呼叫 recall 產生的 offered 不算平台注入證據。
- `applied` 顯式訊號：介面必交付，且至少在 Claude 平台（hook 能力最完整）有一條實證。
- 若 `applied` 實證在任何平台都做不到 → PR-F 只帶 `Closes #17`，#18 留 open 並留言記錄已交付部分與剩餘缺口。

## 4. Runtime 恢復序列（PR-A + PR-B merge 後自動執行；維護窗口語義）

前置：以當下實測數字為準（毒快取筆數、孤兒進程數以 doctor 輸出為據，不沿用快照數字）。

1. **Backend 前置驗證（gate）**：執行既有設定 migration（§3.1.7）→ 以 systemd service-effective 環境跑 backend probe（實際喚起 backend 一次）。**probe 不過 → 序列中止**，否則清快取後只會把積壓 session 推進 `parked`。
2. **Quiesce（停止所有 writer）**：`systemctl --user stop` dream timer/service → 終止孤兒 dream loop（驗證 cmdline 確為暫存 worktree 路徑 → SIGTERM → 確認退出）→ 確認無 importer 進程 → 取得 global dream lock 並全程持有。**備份與變更操作不得與任何 writer 並行。**
3. **完整備份**：快照所有 dream 會寫入的目錄——ledger、queue、LLM cache、`indexes/`、`knowledge/`、MOC 檔、`archive/fragments/`——打包至 runtime 外帶時間戳目錄；**驗證備份清單與可解壓性**後才繼續。附實測過的 restore 步驟（收口批次文件化）。
4. **清毒快取**：對所有卡在 `split` 且 retry 超限的 session：刪 LLM cache + retry sidecar（保留 split fragments，見 §3.1.8）。
5. **觸發 dream run**：釋放 global lock → 重啟 systemd timer/service → 手動觸發一輪，觀察 atomize/MOC/reindex 全鏈。
6. **驗證**（全部成立才算成功）：
   - 新 slices > 0 或全部 pending session 進入明確 terminal state（promoted/skipped/parked）。
   - 失敗顯性：`parked` 有 category 與證據、`_failed/` 非空（若有失敗）。
   - `indexed IDs == eligible IDs`（三方對賬版）。
   - 單一 dream writer（flock 生效）。
7. **失敗處理**：任一步不過 → 序列停止、保留現場與備份、回報人工介入（附 restore 指引）；不自動回滾已 merge 的 code。
8. legacy locks 清理在 PR-C merge 後、同一 quiesce 語義的維護窗口補執行。

## 5. 收口批次（全部 merge + 恢復完成後）

1. **#20 重採樣**：以新指標產出快照留言（promoted/parked/retrying、eligible/indexed、per-tool offered/read/applied、active dream owner、lock anchors 數），附 before/after 表；**不覆寫原快照**（保留歷史基線）；關閉 #20 與其 checklist 內全部子項。
2. **#9 驗收材料**：交付一鍵驗收腳本（檢查 timer enabled/active、list-timers 有下次排程、journal 無 backend/path error、dream ledger timestamp 晚於 boot）；issue 留言更新已完成項與「reboot 後跑腳本」指引；**#9 保持 open 等真機驗收**。
3. 各 code PR merge 時以 `Closes #N` 自動關對應 issue。

## 6. Workflow 編排規格（ultracode）

- **輸入**：本 spec 對應的 6 份 plan（writing-plans 產出）路徑 + 相依圖。
- **拓撲**：`A、B、E` 立即並行 → `C` await A → `D` await C（C、D 都動 `ops.py`，明確序列化，不再並行）→ `F` await E → 恢復序列 await A+B → 收口 await 全部。
- **每批次管線**（各自 git worktree）：
  1. implement agent（帶 plan 全文，TDD）
  2. test agent（targeted + 全套 pytest）
  3. adversarial verify ×3 lens 並行（正確性／policy 合規／迴歸風險）。**fail-closed 裁決**：任一 lens 報 blocking severity（critical/high）→ 該批 fail，不得以多數決覆蓋——三個 lens 是互補視角，不是冗餘投票。
  4. fix loop ≤ 2 輪（verify fail → fix agent → re-verify）
  5. **merge gate（在最新 main 上重驗）**：rebase 到 latest main → 重跑全套 pytest + `python3 -m policy_check --repo .` + changelog.d 碎片存在。sibling 先合入導致綠燈失效 → 回 fix loop。
  6. 開 PR（title conventional-commit、body `Closes #N` + checklist 全勾、zh-tw）→ `gh pr merge --squash`
- **失敗語義**：批次 2 輪 fix 仍 fail → 標 `blocked`、不 merge；下游批次不啟動、如實回報。merge conflict → rebase 重試一次，再衝突 → 人工介入點。
- **產出**：每批次回報 {branch, PR url, merge SHA, 測試摘要, verify 裁決}；恢復序列回報每步證據；收口回報 issue 連結。

## 7. 合規要求（policy v1.0.12，全批次一體適用）

- 分支一律 `feature/<issue>-<slug>`；禁 commit main。
- 每 code PR：changelog.d 碎片（repo 現行慣例）、PR checklist 全勾、`Closes #N`（R-17）、zh-tw（語言規範）、`policy_check` 零 failure。
- `tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。
- R-18/R-22：behavior 變更同步 README／docs 引用（`hippo recall`、`--backend` 選單、doctor 新輸出）。
- 測試新增全部進 CI 覆蓋（R-19；`tests.yml` 已自動跑 pytest）。

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| PR-A/C/D 都動 `ops.py` | 完全串行化：A → C → D（不只 await A） |
| stale-base 綠燈失效 | merge gate 一律 rebase latest main 後重跑全套驗證 |
| verify 誤放行 | 任一 lens blocking finding 即 fail-closed，無多數決 |
| 真蒸餾 smoke 受各 CLI 認證/配額影響 | smoke 標記可重試；CLI 不可用時該 preset 標 skip 並回報，不擋批次但影響 #10 關單條件（§3.5） |
| 恢復序列動真實 runtime | quiesce 全部 writer + 完整備份（含 knowledge/MOC/archive）+ 驗證可解壓 + backend probe 前置 gate |
| 恢復後無法還原 | 備份附實測 restore 步驟；失敗即停、保留現場 |
| gemini/copilot headless 介面與預期不符 | preset 以實測為準；接不上就 registry 標 unavailable + 回報，不猜 argv |
| 過早關單（#10/#14/#18） | 條件式 Closes：實證不齊改 Refs + 拆殘項／留 open（§3.3、§3.5、§3.6） |
| 併發 workflow agents 撞 runtime | 只有恢復序列碰 runtime，且為單線串行 + global lock |
