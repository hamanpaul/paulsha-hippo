### Fixed
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- dream singleton（#19）：`dream run` 入口以 `<memory_root>/runtime/locks/dream.lock` 全域 nonblocking flock 整輪持有，取不到鎖記 log 後 skip（exit 0），杜絕並發競寫。
- dream orchestrator 錯誤可見性（#19）：pass 失敗保存 bounded 錯誤訊息（≤500 字元、去敏）與 errno，不再只存 exception 類別名。
- backend 絕對路徑（#10 最小修復）：`hippo init` 產生 atomizer override 時 argv[0] 經 `resolve_backend_argv` 絕對路徑化（systemd 環境無 NVM PATH 也找得到）；`hippo doctor` 以 service-effective PATH probe 蒸餾 backend。

### Added
- `hippo requeue <session-key>|--all-parked`：parked session 回 `split` 重走 promote（ledger 記 `requeued_from`／`requeue_reason`）。
- `hippo doctor --fix-backend`：冪等遷移既有 atomizer override 的裸 backend 命令為絕對路徑（先備份 `.bak`）。
- `paulsha_hippo/dream/lock.py`：global dream lock（`dream_lock_path`／`acquire_dream_lock`），PR-C doctor 引用同一路徑。
- `paulsha_hippo/ops.py`：`resolve_backend_argv` + `BackendUnavailableError`（PR-D preset registry 重用）。
