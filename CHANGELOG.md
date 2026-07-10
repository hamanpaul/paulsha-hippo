# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [Unreleased]

### Added
- dream `--require-idle` 增加記憶體 headroom 閘，新增 `--min-avail-mem-pct`（預設 20%）並在低記憶體時輸出 `skipped: "low memory"` 與 `avail_pct`。
- `hippo dream supervise` 偵測 systemd dream timer 已接管時會讓位，避免與 timer 雙跑。
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳、隨索引寫入 `retrieval.db` 的 `coverage` 表（權威來源，與索引成對原子發布）並派生 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。DB 反查驗到實際提供搜尋結果的兩張表（slice_meta ↔ slices_fts slice_id multiset 一對一 + FTS integrity-check）——FTS 缺行／幽靈行／重複行而 metadata 完整時不再 false green。
- Project registry（#14）：importer ingest 後將已解析的 project mapping（slug/roots/remotes）寫入 generated 檔 `paulsha/project-hippo.yaml`——schema_version 1、deterministic 輸出（排序去重、逐 byte 可重現）、remote 正規化去 credential、worktree 歸併主 repo root（slug 與 roots 同源——linked worktree 的 discovery slug 以主 repo root 重新推導）、discovery 寫入 gate（僅 slug 由 remote 正規化派生才寫入，且驗證逐 remote 套用——僅個別通過驗證的 remote 落盤，payload 夾帶的不相干 remote 不得搭便車；dir-name/basename fallback slug 一律 skip 並記 debug log——杜絕 remoteless worktree 矛盾 mapping 汙染主 repo 歸屬、與已刪 cwd／git 逾時下垃圾 slug 掛真 remote 的自我強化污染；path 形 repo 欄位不入 remotes）、temp+atomic replace+固定名 lock、寫入端 slug 驗證與 reader 丟棄邊界對齊（全空白 slug 拒收 `ValueError`——否則落盤後 reader 解析時靜默丟棄、下一筆任意 discovery 重繪即無聲永久抹除該 entry）、schema_version 前向防護（既有檔版本高於 producer 支援時拒寫不降級，避免混版部署刪除新版欄位）、opt-in `project_registry.auto_write`（預設 off）、fail-open；讀取端（resolve_project 預設載入）union-read legacy `projects.yaml` 與新檔（非破壞過渡）；檔案契約文件 `docs/project-registry-contract.md` 由 producer contract test 逐 byte 錨定。
- `hippo requeue <session-key>|--all-parked`：parked session 回 `split` 重走 promote（ledger 記 `requeued_from`／`requeue_reason`）。
- `hippo doctor --fix-backend`：冪等遷移既有 atomizer override 的裸 backend 命令為絕對路徑（先備份 `.bak`）。
- `paulsha_hippo/dream/lock.py`：global dream lock（`dream_lock_path`／`acquire_dream_lock`），PR-C doctor 引用同一路徑。
- `paulsha_hippo/ops.py`：`resolve_backend_argv` + `BackendUnavailableError`（PR-D preset registry 重用）。

### Changed
- dream systemd timer 排程改為 `OnCalendar=hourly`，並保留 `Persistent=true`。
- dream systemd service 新增 `CPUWeight=20`、`MemoryHigh=20%`、`MemoryMax=30%`、`TasksMax=256` 的可攜 cgroup 資源上限，且不設 `CPUQuota`。

### Fixed
- `install service` 生成的 systemd unit：`ExecStart` 綁定當前 interpreter（`sys.executable`），修正 pipx / venv 隔離安裝下寫死 `/usr/bin/env python3`（全域 python）import 不到 `paulsha_hippo`、導致 dream service 一觸發即 `exit 1`（ModuleNotFoundError）的問題。
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。
- `build_index()` 並發安全：全程持 `runtime/locks/index-rebuild.lock`（阻塞式 flock）序列化所有 index writer（dream／rekey／retitle 任意呼叫路徑），temp DB 與 coverage 落盤改用 per-invocation 唯一暫存路徑——交錯的並發重建不再可能把對方未完成的索引發布成正式版。
- `build_index()` 發布視窗收口：coverage 併入同一顆 temp DB（`coverage` 表），與索引由**單次** `os.replace` 原子發布——coverage 寫入失敗（如 ENOSPC）或程序在兩步間終止，不再留下「新 DB＋舊/缺 coverage」的半發布狀態，失敗時舊索引與舊 coverage 完整保留；`retrieval.coverage.json` 改為發布成功後的派生輸出（衍生失敗僅記 warning，不推翻已發布索引），`hippo index verify` 改以 DB 內 coverage 為權威來源（無 coverage 表的舊版 DB 退回讀派生 JSON）。
- `build_index()` 對磁碟上重複 `slice_id`（naming dedup fail-soft 跳過後的殘留態）fail-soft：掃描迴圈先到先贏去重，後到者歸 `pool_excluded[duplicate-slice-id-on-disk]` 並記 warning——不再讓 `slice_meta` PK 的 `IntegrityError` 炸掉整批重建、連健康無關 slices 都退回舊索引；census 對賬鏡像同一規則（分佈對齊），`duplicate slice_id on disk` 仍由 `hippo index verify` 顯性回報。
- census 三方對賬的 fate/eligible 身份改以自身 line-based 獨立解析（`CensusEntry.slice_id/memory_layer`）為基準，並逐檔與 `fio.read` 交叉比對、任何 identity divergence 記入 problems——與 build_index 共用的 parser 誤判磁碟 ID（合法 YAML tag/anchor 如 `!!str sl-x`、或 parser bug）時，eligible 端與 DB 端不再拿到同一個錯 ID 而 false green（spec §3.2 防同源自證）。
- `build_index()` row 正規化嚴格驗證 `tags` 型別（必須為 list[str]；缺欄/null 視為空）＋逐檔分類全程包 per-slice 例外邊界：合法 YAML 的 `tags: [1]` 之類錯型歸 `invalid_frontmatter` 記路徑 warning、非預期分類例外亦只犧牲該檔——不再讓單一毒 slice 的 `TypeError` 炸掉整批重建、健康 slices 不發布或持續供應 stale index；census 雙寫同一 tags 型別規則（分佈對齊）。
- Project registry（#14）YAML quoting：`render_registry` 原樣插值動態值（slug／roots／remotes／aliases），含 `#`、`: `、`[]` 等合法字元的值（如 `/tmp/team #1/widget`）會被標準 YAML parser 誤讀（截斷成註解、巢狀 mapping、flow list）——獨立 consumer（cortex）靜默掉專案或拿錯路徑。修正：動態值一律輸出 double-quoted scalar（僅 escape `\` 與 `"`；stdlib-only），`parse_registry` 同步支援 quoted 形（unquote＋unescape、quote-aware inline list）並容忍 legacy plain 形；fixture／契約文件同步（表層格式調整、標準 YAML 語義不變，依契約 §7 不 bump schema_version）；新增 PyYAML oracle contract tests（僅測試側依賴）錨定特殊字元讀回原值。
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- atomize 初始化失敗邊界（#15 review＋Codex 複驗）：atomizer config 載入／promoter 建構抽為 dream 與直呼 `hippo atomize` 共用邊界（`prepare_pipeline_inputs`／`park_init_failure`）——初始化失敗分類 `backend_unavailable`、eligible split sessions 立即 park（含證據）；dream 路徑由 dream ledger 記 error record，直呼 `hippo atomize` 以結構化 JSON 錯誤收斂（exit 1），不再 traceback 逃逸、session 卡在 split 且 `_failed/` 無證據（spec「config 無效立即 parked」不分入口）；janitor config 失敗同樣入 pass 隔離邊界。park 時從磁碟殘留反推並清除該 session「所有」LLM cache／retry sidecar 變體，證據記真實 cache_key／attempts（spec §3.1「進 parked 即淘汰」對每條進入路徑無條件成立）——requeue 後不再繼承過期 retry 計數而提前重新 park。
- doctor backend probe（#15 review＋Codex 複驗）：service-effective PATH 下對完整 backend argv 送 bounded smoke prompt（stdin 餵入、受限 timeout）並 fail-closed 判定——timeout 內 exit 0 且回應非空才 PASS；timeout（backend hang）、任何非零 exit（認證／model／quota／config 錯誤與 126/127）、空輸出、exec 失敗（shebang／interpreter 斷鏈、`/usr/bin/env <missing-runtime>`）一律 FAIL。openai-compatible 檔位不再「PR-D 接手」綠燈，改以 `HttpAgentClient` 直打 `/v1/chat/completions` 做等價 smoke probe（bounded max_tokens／timeout）。probe env 比照 `agent_exec` 注入 `HIPPO_SELF_SESSION=1`，避免探測被使用者已裝的 SessionEnd/PreCompact hooks 當成真實 session 寫回 queue（#7 遞迴自捕捉回歸防護）。
- doctor probe 環境忠實化（#15 Codex 複驗 B1）：argv 與 openai-compatible probe 不再繼承互動 shell 的 os.environ（僅替換 PATH）——已安裝 oneshot unit 無 `Environment=`/`EnvironmentFile=`，排程實際繼承的是 systemd user manager 環境；probe env 改以 `systemctl --user show-environment` 顯式構造（保留 `HIPPO_SELF_SESSION=1` 注入；`HttpAgentClient` 新增 `env` 注入以從 manager env 解析 API key），API key 只 export 在互動 shell 時 doctor 不再誤判健康（requeue 後 dream service 認證失敗再度 parked），反向（key 只設在 manager env）也不再誤判故障；無 systemd user bus（CI 等）fallback 現行近似並於報告行標示「近似，非 service-effective」。
- requeue 零 fragment gate（#15 Codex 複驗 B2）：`hippo requeue` 在提交 split「之前」驗證至少一個可讀且屬於該 session 的 fragment（早前 `_fragment_count` 在 append_state 之後才算、純回報用）——缺失時維持 parked、計入 skipped（reason `no-fragments`），CLI 回非零 exit 並於 stderr 說明；杜絕 zero-fragment 的 parked session 被 requeue 成 split 後永久卡非終態（pipeline 無 fragment 可 promote）且 exit 0 誤報成功。
- dream lock errno 分辨（#15 review）：`acquire_dream_lock` 只把 contention（`BlockingIOError`／`EAGAIN`／`EACCES`）視為「another process」，`ENOLCK`／`EIO` 等其餘 OSError 上拋讓 dream 非零收場，鎖層故障不再偽裝成功吞掉 backlog。
- dream singleton（#19）：`dream run` 入口以 `<memory_root>/runtime/locks/dream.lock` 全域 nonblocking flock 整輪持有，取不到鎖記 log 後 skip（exit 0），杜絕並發競寫。
- dream orchestrator 錯誤可見性（#19）：pass 失敗保存 bounded 錯誤訊息（≤500 字元、去敏）與 errno，不再只存 exception 類別名。
- backend 絕對路徑（#10 最小修復）：`hippo init` 產生 atomizer override 時 argv[0] 經 `resolve_backend_argv` 絕對路徑化（systemd 環境無 NVM PATH 也找得到）；`hippo doctor` 以 service-effective PATH probe 蒸餾 backend。

### Security
- parked／dream ledger 去敏升級（#15 review）：`sanitize_error_text`、`_failed` 證據（`error`／`last_output_excerpt`）與 dream record warnings 落盤前套用 policy 既有 secret redaction（GitHub PAT／Bearer／OpenAI・Anthropic／AWS key／JWT 等），且 redaction 先於截斷；redaction 機制失效時 fail-closed 以 placeholder 取代——credential 不落任何持久化副本。
- 持久化 scrub 不可被 override 弱化（#15 Codex 複驗）：`redact_secret_text` 改以 `load_policy(override_path=None)` 載入 immutable baseline 規則——使用者 `policy.override.yaml` 的 `disable_rules`／`disable_rules_for_session` 只影響蒸餾管線，不再能停用持久化出口的強制去敏（先前 `disable_rules: [github_pat]` 會讓 PAT 原文落 `_failed/*.json`／processing.jsonl／dream.jsonl）；補 sanitize＋三持久化出口的全域停用情境回歸測試。

## [0.1.0] - 2026-07-07

### Added
- #125 Phase 1 code 遷入：paulshaclaw `memory/**`（31k LOC、872 tests 綠）平移為 `paulsha_hippo/**`；`lifecycle` → `lib/lifecycle`、`idle` → `lib/idle`；hippo CLI 樹去 `memory` 前綴（`hippo atomize|dream|janitor|replay|bundle|search|wakeup|syncback|knowledge`）；`paths.py` 單一權威 resolver（`HIPPO_*` > `PSC_*` deprecated 警告 > config.yaml > `~/.agents/memory`）；stage2 12 份 capability specs、integration check、gemma4 wrapper（scripts/ + examples/）隨遷
- 隨遷主 repo `tests/` 下漏網的 memory 測試：policy 三件（boundary/redaction/lint/cli，54 tests）與 #218 hooks 截取自足回歸 2 件
- quickstart 面：`hippo init`（backend preset 寫入 atomizer override）、`hippo doctor`（雙 root FAIL 健檢）、`hippo install hooks|service`（systemd 偵測＋`dream supervise` fallback）、`hippo dream supervise`（前景常駐）；蒸餾三檔位——`claude-headless`（零 key）、`openai-compatible`（stdlib http-runner）、`custom-argv`（既有 agent_exec）
- repo 骨架：conventions 引擎 1.0.12（pin 5829015）+ `tier: shareable`（R-21 deident gate day-1）、package 0.1.0 與 `hippo --version` 入口、版號一致性測試、`paulsha_hippo.lib` import 隔離護欄、骨架期 README
- `paulsha_hippo.lib.session_readers`：`read_codex_rollout`/`read_copilot_history` 升格 lib API（hippo importer + paulshaclaw bro hook 兩使用者；adapters.base 保留 re-export）

### Fixed
- #7 遞迴自捕捉：agent_exec 對蒸餾子程序注入 `HIPPO_SELF_SESSION=1`，5 個 capture hook（session_end×3／precompact×2）讀到即早退（layer 1）；importer 對 prompt 內容即 atomize skill 調用文本者 `self-skip`（layer 2）
- #8 空 session 汙染：importer 對無 prompt/無 touched files/summary 空或佔位/turn≤1 的 session `empty-skip`，不寫 inbox、不入蒸餾佇列
- wheel/pipx 情境 `hippo install hooks`：repo_root 無 pyproject 時 importer venv 改複製已解包套件（原 pip install -e 必失敗）；sample yaml 隨包（config-samples/）
- wheel 安裝缺非 .py 資產（hooks install.sh／dream systemd 範本／atomizer.yaml／skills）——補 package-data 宣告；fresh-install E2E 驗證抓到
