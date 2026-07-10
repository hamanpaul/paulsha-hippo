### Fixed
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- atomize 初始化失敗邊界（#15 review＋Codex 複驗）：atomizer config 載入／promoter 建構抽為 dream 與直呼 `hippo atomize` 共用邊界（`prepare_pipeline_inputs`／`park_init_failure`）——初始化失敗分類 `backend_unavailable`、eligible split sessions 立即 park（含證據）；dream 路徑由 dream ledger 記 error record，直呼 `hippo atomize` 以結構化 JSON 錯誤收斂（exit 1），不再 traceback 逃逸、session 卡在 split 且 `_failed/` 無證據（spec「config 無效立即 parked」不分入口）；janitor config 失敗同樣入 pass 隔離邊界。park 時從磁碟殘留反推並清除該 session「所有」LLM cache／retry sidecar 變體，證據記真實 cache_key／attempts（spec §3.1「進 parked 即淘汰」對每條進入路徑無條件成立）——requeue 後不再繼承過期 retry 計數而提前重新 park。
- doctor backend probe（#15 review＋Codex 複驗）：service-effective PATH 下對完整 backend argv 送 bounded smoke prompt（stdin 餵入、受限 timeout）並 fail-closed 判定——timeout 內 exit 0 且回應非空才 PASS；timeout（backend hang）、任何非零 exit（認證／model／quota／config 錯誤與 126/127）、空輸出、exec 失敗（shebang／interpreter 斷鏈、`/usr/bin/env <missing-runtime>`）一律 FAIL。openai-compatible 檔位不再「PR-D 接手」綠燈，改以 `HttpAgentClient` 直打 `/v1/chat/completions` 做等價 smoke probe（bounded max_tokens／timeout）。probe env 比照 `agent_exec` 注入 `HIPPO_SELF_SESSION=1`，避免探測被使用者已裝的 SessionEnd/PreCompact hooks 當成真實 session 寫回 queue（#7 遞迴自捕捉回歸防護）。
- doctor probe 環境忠實化（#15 Codex 複驗 B1）：argv 與 openai-compatible probe 不再繼承互動 shell 的 os.environ（僅替換 PATH）——已安裝 oneshot unit 無 `Environment=`/`EnvironmentFile=`，排程實際繼承的是 systemd user manager 環境；probe env 改以 `systemctl --user show-environment` 顯式構造（保留 `HIPPO_SELF_SESSION=1` 注入；`HttpAgentClient` 新增 `env` 注入以從 manager env 解析 API key），API key 只 export 在互動 shell 時 doctor 不再誤判健康（requeue 後 dream service 認證失敗再度 parked），反向（key 只設在 manager env）也不再誤判故障；無 systemd user bus（CI 等）fallback 現行近似並於報告行標示「近似，非 service-effective」。
- dream lock errno 分辨（#15 review）：`acquire_dream_lock` 只把 contention（`BlockingIOError`／`EAGAIN`／`EACCES`）視為「another process」，`ENOLCK`／`EIO` 等其餘 OSError 上拋讓 dream 非零收場，鎖層故障不再偽裝成功吞掉 backlog。
- dream singleton（#19）：`dream run` 入口以 `<memory_root>/runtime/locks/dream.lock` 全域 nonblocking flock 整輪持有，取不到鎖記 log 後 skip（exit 0），杜絕並發競寫。
- dream orchestrator 錯誤可見性（#19）：pass 失敗保存 bounded 錯誤訊息（≤500 字元、去敏）與 errno，不再只存 exception 類別名。
- backend 絕對路徑（#10 最小修復）：`hippo init` 產生 atomizer override 時 argv[0] 經 `resolve_backend_argv` 絕對路徑化（systemd 環境無 NVM PATH 也找得到）；`hippo doctor` 以 service-effective PATH probe 蒸餾 backend。

### Security
- parked／dream ledger 去敏升級（#15 review）：`sanitize_error_text`、`_failed` 證據（`error`／`last_output_excerpt`）與 dream record warnings 落盤前套用 policy 既有 secret redaction（GitHub PAT／Bearer／OpenAI・Anthropic／AWS key／JWT 等），且 redaction 先於截斷；redaction 機制失效時 fail-closed 以 placeholder 取代——credential 不落任何持久化副本。
- 持久化 scrub 不可被 override 弱化（#15 Codex 複驗）：`redact_secret_text` 改以 `load_policy(override_path=None)` 載入 immutable baseline 規則——使用者 `policy.override.yaml` 的 `disable_rules`／`disable_rules_for_session` 只影響蒸餾管線，不再能停用持久化出口的強制去敏（先前 `disable_rules: [github_pat]` 會讓 PAT 原文落 `_failed/*.json`／processing.jsonl／dream.jsonl）；補 sanitize＋三持久化出口的全域停用情境回歸測試。

### Added
- `hippo requeue <session-key>|--all-parked`：parked session 回 `split` 重走 promote（ledger 記 `requeued_from`／`requeue_reason`）。
- `hippo doctor --fix-backend`：冪等遷移既有 atomizer override 的裸 backend 命令為絕對路徑（先備份 `.bak`）。
- `paulsha_hippo/dream/lock.py`：global dream lock（`dream_lock_path`／`acquire_dream_lock`），PR-C doctor 引用同一路徑。
- `paulsha_hippo/ops.py`：`resolve_backend_argv` + `BackendUnavailableError`（PR-D preset registry 重用）。
