---
status: accepted
work_item: issue-157-distiller-chain
---

# Issue #157 蒸餾鏈三段式浪費設計

- 日期：2026-09-27
- Issue：[#157](https://github.com/hamanpaul/paulsha-hippo/issues/157)

## 證據（以 main 為準）

- 失敗佇列：`attempts_detail` 為空的 `backend_unavailable` 紀錄與同一 run 內另一筆真實失敗紀錄共用同一個 `ts`；那一筆的三個 attempt（cg／claude／codex）合計約 7 秒。router 對 circuit-open profile 靜默 `continue`，`_raise_exhausted` 拿到 `last=None`。以 `tests/test_router_skip_reasons.py` 用兩個 session 的 pipeline 重現。
- cg：以無憑證的拋棄式 HOME 直接呼叫 copilot CLI 1.0.88，`-p 'hello'` 進到認證階段，`-p $'---\n…'` 立即回 `error: Invalid command format`；`--prompt=$'---\n…'` 正常進到認證階段。
- claude：以 main 的 `budget.pack_prompt_chunks` 對合成 session 組 prompt，用 main 的 claude argv 呼叫 6 次，4 次輸出被包進 ```json fence 或前置散文；只加 `--system-prompt` 後 8/8 合法，改走 Hippo 真實 router／`AgentExecClient` 路徑再 3/3 合法，title 類純文字任務照常輸出單行標題。
- 「約 11 分鐘」：以 ledger 的 `chunk_provenance` 對照，658 秒那次 attempt 內 claude 已驗證 3 個 chunk（約 604 秒），失敗的第 4 個 chunk 約 54 秒；attempt 層級的 `elapsed_seconds` 包含已保留的 chunk。

## Decisions

### D1：circuit-open 略過紀錄是 provenance，不是 terminal

略過紀錄沿用既有 ineligible 風格（`failure_category="ineligible"`、零 elapsed、不消耗 agent call），原因寫在 stderr。它們不計入 `max_attempts`，否則被略過的 profile 會把後面仍健康的 profile 擠掉。真實失敗之後的略過紀錄不成為 terminal，park 分類與既有 #106 契約一致；只有「全部都被略過、沒有任何真實 attempt」時才以最後一筆略過紀錄作為 terminal，分類仍是原本的 `ineligible`（→ `backend_unavailable`），差別只在錯誤訊息帶有逐一原因。chain-budget 中斷時，circuit-open 的剩餘 profile 改記自己的 circuit 原因，而不是被省略或記成 `session_deadline`。

### D2：只有確定性失敗進持久退避

誤判退避的代價是 session 被 park（需手動 requeue），比多試一次高得多，所以規則刻意保守：nonzero exit、30 秒內失敗，且 sanitized stderr 開頭 160 字元內以 CLI 參數解析錯誤（`invocation`）或憑證錯誤（`credential`）開頭。只看開頭，是因為 codex 會把 prompt 回顯到 stderr。`invocation` 一次即退避，`credential` 連續兩個 session 才退避。退避 1 小時起跳、每次加倍、上限 24 小時；到期允許一次探測。成功或非確定性結果清除狀態；`budget`／`ineligible` 是 router 自己的判斷，不提供訊號。狀態以 command fingerprint 綁定，argv 變更即失效。

### D3：狀態檔與可見性

狀態寫在 `<memory_root>/runtime/agents/profile-health.json`，依 task class 分開（`task_classes.<task_class>.<profile_id>`）。router 透過 `ProfileHealth` 協定讀寫並帶上自己的 task class，任何例外都吞掉，不讓建議性狀態影響蒸餾。dream、直呼 `hippo atomize`（不取 dream lock）、hook 裡的 title importer、skillopt 可能同時寫入：凡是可能改變狀態的判斷都在同目錄 `profile-health.json.lock` 的 flock 內重讀後才決定（鎖檔永不 unlink；不放在 `runtime/locks/`，以免被 `hippo locks cleanup-legacy` 歸為未知鎖檔而擋下 `--apply`），包括成功時是否需要清除——鎖外讀到的「沒有狀態」可能在返回前被並行的失敗寫入推翻（PR #162 第二輪審查）。唯一不取鎖的快速路徑是唯讀 store 與 `budget`／`ineligible` 結果，兩者與檔案內容無關、一定不寫入。取鎖比照 `deployment._writer_lock` 以 `LOCK_EX|LOCK_NB` 輪詢到 2 秒期限，逾時放棄該次更新並記 warning（fail-open），同一 store 之後 60 秒內直接放棄更新、不再每個 attempt 等滿期限；期限計時綁定模組載入時的 `time.monotonic`，不受測試替換的假時鐘影響。暫存檔名每次唯一（比照 `moc.search._unique_tmp`）。讀取不取鎖：檔案只以 `os.replace` 整檔替換。讀檔失敗或毀損視為無狀態，寫檔或取鎖失敗只記 warning。dry-run 使用唯讀 store。

### D6：title 與 skillopt 路徑共用 store，但依 task class 分開

skillopt rollout 的 router 是 `atomization` task class、送同一份以 `---` 開頭的 atomize prompt，cg 的呼叫格式錯誤在這條路徑同樣必敗，因此與 dream 共用 `atomization` 狀態。title importer 在每個 session 結束的 hook 裡跑，憑證失效這類確定性失敗若不退避會每次重打外部 CLI。title prompt 以中文開頭，cg 在 title 路徑不會觸發呼叫格式錯誤；若所有 task class 共用一筆狀態，title 的成功會清掉 atomization 的退避、兩邊來回翻轉，所以狀態依 task class 分開。title 的 `_default_runner(text, command, timeout)` 是既有替換點，memory root 以 ContextVar 從 `generate_title`／`generate_atom_title` 的 `memory_root` 參數傳入，沒有 memory root 時不讀寫狀態。`hippo doctor` 以唯讀 store 在 profile 行尾顯示 `health=backoff(...)`／`degraded`／`probe-pending`／`stale` 與最後 stderr 摘要，不改 exit code。

### D4：claude 以 `--system-prompt` 修根因，不放寬 parser

spec 明訂 canonical response 的前後 noise 無效，parser 維持嚴格；改在呼叫端移除衝突來源。系統提示只約束輸出框架、不描述 schema，atomization／title／skillopt 共用。字串通過既有 argv 安全驗證。`atomizer.yaml` 與 `default_profiles()` 同步，由既有模板漂移守門測試保護。部署中的使用者 config 需另外同步（repo 外）。

### D5：cg launcher 用 `--prompt=` 綁定

launcher 屬 `contrib/`，不進 wheel；修正後仍需部署到 `~/.local/bin/`。cg 背後的 llm-share 訂閱已停止，修正呼叫格式後預期會改成憑證／額度類錯誤——前者會由 D2 退避，後者屬 operator 決策（停用 profile）。

## Testing

- `tests/test_router_skip_reasons.py`：circuit-open 略過紀錄、不佔 attempt budget、真實失敗後 terminal 不變、pipeline park 證據逐一列出原因。
- `tests/test_profile_health_backoff.py`：跨 router 實例退避、到期探測與加倍、上限、成功清除、非確定性失敗不退避（含 codex 回顯與慢失敗）、憑證門檻、command 變更失效、毀損檔、唯讀、doctor 顯示。
- `tests/test_external_agent_profiles.py`：claude argv 的 `--system-prompt` 契約；既有 circuit-open 測試改為驗證新的略過原因。
- `tests/test_copilot_launcher_prompt_binding.py`：假 copilot 記錄 argv，驗證 `--prompt=` 單一 token。
- `tests/test_profile_health_backoff.py`（PR #162 審查）：4 個行程各 30 次並發更新不遺失；佔住舊的固定暫存檔名仍寫得進去。
- `tests/test_profile_health_backoff.py`（PR #162 第二輪審查）：成功與鎖內寫入中的失敗並行時仍清除退避；另一行程持鎖不放時記錄在期限內返回、寫 log、之後不再等鎖、讀取不卡住；routing 照常完成。
- `tests/test_profile_health_wiring.py`（PR #162 審查）：title importer 記錄並遵守退避；沒有 memory root 時不寫狀態；skillopt 三個 router 共用 store（dry-run 唯讀）；skillopt rollout 遵守 dream 記下的 atomization 退避；task class 互不影響。
