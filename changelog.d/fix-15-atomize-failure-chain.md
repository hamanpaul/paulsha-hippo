### Fixed
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- dream run 初始化失敗邊界（#15 review）：atomizer config 載入／promoter 建構失敗不再逃出 `run_dream` 記錄邊界——分類 `backend_unavailable`、eligible split sessions 立即 park（含證據），dream ledger 記 error record（spec「config 無效立即 parked」）；janitor config 失敗同樣入 pass 隔離邊界。park 時從磁碟殘留反推並清除該 session「所有」LLM cache／retry sidecar 變體，證據記真實 cache_key／attempts（spec §3.1「進 parked 即淘汰」對每條進入路徑無條件成立）——requeue 後不再繼承過期 retry 計數而提前重新 park。
- doctor backend probe（#15 review）：service-effective PATH 下「實際執行」完整 backend argv（受限 timeout、stdin 關閉），抓 shebang／interpreter 斷鏈與 `/usr/bin/env <missing-runtime>` 型錯誤綠燈；exec 失敗與 exit 126/127 → FAIL，timeout／業務性非零退出視為可執行。probe env 比照 `agent_exec` 注入 `HIPPO_SELF_SESSION=1`，避免探測被使用者已裝的 SessionEnd/PreCompact hooks 當成真實 session 寫回 queue（#7 遞迴自捕捉回歸防護）。
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
