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

狀態寫在 `<memory_root>/runtime/agents/profile-health.json`（原子寫入；讀檔失敗或毀損視為無狀態；寫檔失敗只記 warning）。router 透過 `ProfileHealth` 協定讀寫，任何例外都吞掉，不讓建議性狀態影響蒸餾。dry-run 使用唯讀 store。`hippo doctor` 以唯讀 store 在 profile 行尾顯示 `health=backoff(...)`／`degraded`／`probe-pending`／`stale` 與最後 stderr 摘要，不改 exit code。

### D4：claude 以 `--system-prompt` 修根因，不放寬 parser

spec 明訂 canonical response 的前後 noise 無效，parser 維持嚴格；改在呼叫端移除衝突來源。系統提示只約束輸出框架、不描述 schema，atomization／title／skillopt 共用。字串通過既有 argv 安全驗證。`atomizer.yaml` 與 `default_profiles()` 同步，由既有模板漂移守門測試保護。部署中的使用者 config 需另外同步（repo 外）。

### D5：cg launcher 用 `--prompt=` 綁定

launcher 屬 `contrib/`，不進 wheel；修正後仍需部署到 `~/.local/bin/`。cg 背後的 llm-share 訂閱已停止，修正呼叫格式後預期會改成憑證／額度類錯誤——前者會由 D2 退避，後者屬 operator 決策（停用 profile）。

## Testing

- `tests/test_router_skip_reasons.py`：circuit-open 略過紀錄、不佔 attempt budget、真實失敗後 terminal 不變、pipeline park 證據逐一列出原因。
- `tests/test_profile_health_backoff.py`：跨 router 實例退避、到期探測與加倍、上限、成功清除、非確定性失敗不退避（含 codex 回顯與慢失敗）、憑證門檻、command 變更失效、毀損檔、唯讀、doctor 顯示。
- `tests/test_external_agent_profiles.py`：claude argv 的 `--system-prompt` 契約；既有 circuit-open 測試改為驗證新的略過原因。
- `tests/test_copilot_launcher_prompt_binding.py`：假 copilot 記錄 argv，驗證 `--prompt=` 單一 token。
