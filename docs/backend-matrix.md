# External headless profile 矩陣

這份矩陣描述目前 release candidate 的 runtime contract。Hippo 不提供 HTTP/TCP
provider client，也不保存 API key、OAuth、provider URL 或 credential env-name；
登入與 launcher 由外部 CLI 自己負責。

| tier | profile | traits / task class | default model / effort | tokenized headless argv | 狀態 |
|---|---|---|---|---|---|
| 1 | `claude` | judge、reasoner / atomization、title | `sonnet` / `high` | `claude --model {MODEL} --effort {EFFORT} --safe-mode ... --tools '' ... --system-prompt <輸出契約> --print` | 內建 tools/MCP/customizations 停用；`--system-prompt` 取代預設（要求 Markdown 排版的）系統提示；仍須 service-effective live probe |
| 1 | `codex` | judge、reasoner / atomization、title | `gpt-5.6-sol` / `high` | `codex exec --model {MODEL} -c model_reasoning_effort=high ... --ignore-user-config --disable shell_tool -` | 預設 model 已以本機 external CLI real probe 驗證；model 仍由 profile 自訂，其他 effort 必須在 argv 明確映射後再 probe |
| 2 | `agy` | fast、responsive / title | `default` / `medium` | `agy --model {MODEL} --effort {EFFORT} --mode plan --sandbox --print` | 原生 CLI 無可證明的 zero-tool flag，預設不進 Dream atomization eligible set |
| 2 | `cg` | heavy-implementation、fast / atomization、title | `default` / `high` | `cg --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；alias-only 或 zero-tool 契約未證實時不得啟用 |
| 3 | `co-gem` | low-cost、fallback / atomization、title | `local` / `low` | `co-gem --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；本機 launcher headless smoke 通過後才可啟用 |
| 3 | `claude-gem` | low-cost、fallback / atomization、title | `local` / `low` | `claude-gem --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；本機 executable/契約驗證後才可啟用 |
| 3 | custom local | operator-defined traits/task class/model/effort | operator-defined | tokenized argv；不可含 shell wrapper 或 prompt token | 需 operator 以 profile manifest 配置 |

Router 依 `(tier, priority, profile id)` 決定順序，整個 session 使用同一份 frozen
prompt；每次最多 6 attempts / 6 agent calls、每 chunk 300 秒，失敗 profile 進
circuit cooldown。只允許明確的失敗類別 fallback；安全設定錯誤不會降級繞過。成功
但使用 Tier 2/3 時 provenance 會記 `degraded-success` 與先前 attempts。

### 略過原因與持久退避（#157）

- 失敗的 profile 在同一個 run 內進 60 秒 circuit cooldown；被 circuit 擋下的 profile
  不再靜默消失，而是留下 `ineligible` 略過紀錄（`circuit_open after <category>`），
  不消耗 agent call、也不佔 `max_attempts`。全部 profile 都被略過時，`_failed/` 證據的
  `attempts_detail` 與 `error` 會逐一列出每個 profile 的略過原因。
- 確定性必敗（啟動 30 秒內 nonzero exit，且 stderr 開頭是 CLI 參數解析錯誤或憑證錯誤）
  寫入 `<memory_root>/runtime/agents/profile-health.json` 並指數退避（1 小時起跳、每次加倍、上限
  24 小時；呼叫格式錯誤一次即退避，憑證錯誤連續兩個 session 才退避）。退避中的
  profile 以 `backoff <kind> until <UTC>` 略過；到期後允許一次探測，成功或非確定性
  結果即清除狀態，command 變更後舊狀態不再套用。timeout、invalid_output、quota 等
  不進退避。
- 狀態依 task class 分開記錄：dream／`hippo atomize`／skillopt rollout 記在
  `atomization`，title importer（hook）與 `hippo retitle` 記在 `title`，skillopt 的
  judge／optimizer 記在 `skillopt`。同一個 profile 的確定性失敗可能只在某類 prompt
  出現（cg 的呼叫格式錯誤只在 prompt 以 `-` 開頭時發生），分開記錄才不會互相清掉。
  各路徑可能同時寫入：可能改變狀態的判斷一律在同目錄 `profile-health.json.lock`
  的檔案鎖內重讀後才決定，暫存檔名每次唯一。取鎖最多等 2 秒，等不到就放棄這次
  更新並記一行 warning（fail-open），同一行程之後 60 秒內不再等鎖；讀取端不取鎖、
  不會被卡住。dry-run 只讀不寫。
- `hippo doctor` 的 external agent profiles 段落在該 profile 後面逐 task class 顯示
  `health[<task_class>]=backoff(<kind>) until <UTC>`（或 `degraded`／`probe-pending`／
  `stale`）與最後一次 stderr 摘要；只顯示、不改 exit code。刪除狀態檔即可立即重置。

所有 profile 必須使用 `shell=False` 的 tokenized argv；prompt 只走 stdin。`{PROMPT}`、
shell alias/function、shell metacharacter、`--yolo`、`--autopilot`、permission bypass
（含 `--permission-mode bypassPermissions` 這個旗標值寫法）、tool/MCP/remote fallback
均拒絕。`--permission-mode plan` 亦拒絕：plan mode 是核可工作流，與「直接輸出單一 JSON
文件」的回覆契約衝突，實測會讓 agent 對真實 atomization prompt 回散文請示而非執行；
防寫入靠的是 `--tools ''`（零工具），不是 permission mode。child env 是固定 allowlist，
外部 launcher 可在 Hippo 邊界外處理認證。

每個 profile 另有明確 `enabled` gate；停用 profile 會在 executable probe 前即標成
`ineligible/disabled`，不消耗 agent call。Provenance 同時記錄 tier 與 tier 內 priority。

## 驗證邊界

- `tests/test_external_agent_profiles.py` 與 backend matrix 測試覆蓋 tier ordering、
  safety rejection、bounded fallback、cache separation 與 minimal env。
- `tests/test_atomizer_llm_live.py` 的真 CLI probe 只有在 operator 明確設定 live gate
  時執行；未執行不會被標成 passed。
- service-effective eligibility、每個 profile 的 live smoke、installed hook/service
  chain、三次 systemd soak 與 consumer `offered → Read` 仍是 release readiness matrix
  的待驗證 gates。
