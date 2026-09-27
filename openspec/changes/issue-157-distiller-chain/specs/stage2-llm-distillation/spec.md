## MODIFIED Requirements

### Requirement: Skipped-profile provenance on chain-budget exhaustion

當 session chain budget 在 router 走到每個 enabled、task class 相符的 profile 之前耗盡時——不論是 deadline 在 attempt 前檢查就到期（原因 `session_deadline`），或是 deadline／agent-call budget 在 attempt 中耗盡、chunk 迴圈拋出不可 fallback 的 `budget` 類別（原因 `session_budget`）——router SHALL 在中斷前替每個剩餘的 enabled、task class 相符 profile 各附加一筆 attempt 紀錄，沿用既有 ineligible 風格：`failure_category="ineligible"`、對應的原因字串、零 elapsed、不消耗 agent call。剩餘 profile 若 circuit breaker 目前開啟，或被持久健康狀態留在退避中，即使有預算也不會被執行，因此 SHALL 以該 circuit／退避原因記錄，MUST NOT 記成 budget 略過，也 MUST NOT 被省略。略過紀錄 MUST NOT 改變派送順序、deadline 計算、fallback 語意或 park 行為，兩個原因字串都不得加入 fallback 類別 allowlist。特別是，為 park 而拋出的 exhausted-chain 錯誤 SHALL 從實際執行過的 terminal attempt（或既有 ineligible 路徑記錄的 attempt）取得 `category`、`profile_id`、`exit_code` 與 `stderr`，絕不從附加的略過紀錄取得。由於略過紀錄是在迴圈已決定中斷後才附加，attempts 清單 MAY 超過 `max_attempts`；這些紀錄只是 provenance，由清單長度推導的 park attempt 數因此包含從未執行的 profile，序列化仍受既有 provenance attempt 上限約束。交給 park 的有序 attempt chain SHALL 因此涵蓋每個 enabled、task class 相符的 profile，profile 不會因 chain budget 在評估其資格前耗盡而從 provenance 消失。

#### Scenario: Deadline break records the profiles it skipped
- **WHEN** 先前的 profile attempt 用光整個 chain deadline，router 尚未走到後面 enabled、task class 相符的 profile
- **THEN** 每個被略過的 profile SHALL 以 `ineligible`、原因 `session_deadline` 出現在 attempt provenance，不消耗 agent call，session 其餘 park 行為與先前完全相同——拋出錯誤的 `category`／`profile_id`／`stderr` SHALL 來自最後一個實際執行的 profile

#### Scenario: Mid-attempt budget exhaustion records the profiles it skipped
- **WHEN** deadline 或 agent-call budget 在 attempt 內耗盡（例如多 chunk session 的前幾個 chunk 用光整個 budget），chunk 迴圈拋出不可 fallback 的 `budget` 類別、router 中斷
- **THEN** 每個剩餘的 enabled、task class 相符 profile SHALL 以 `ineligible`、原因 `session_budget` 出現在 attempt provenance，不消耗 agent call，拋出的錯誤 SHALL 回報 terminal 真實 attempt（`category="budget"`、失敗 profile 的 id）

#### Scenario: Circuit-open remaining profiles are recorded with their circuit reason
- **WHEN** chain-budget 中斷發生時，某個剩餘 profile 的 circuit breaker 正開啟
- **THEN** 該 profile SHALL 以 `ineligible`、原因 `circuit_open after <category>` 記錄，MUST NOT 記成 `session_deadline` 或 `session_budget`

#### Scenario: Sufficient budget leaves provenance unchanged
- **WHEN** chain budget 未耗盡，router 走完整條 profile chain，且沒有 profile 處於 circuit-open 或退避
- **THEN** attempt 紀錄 SHALL 與變更前完全相同，不附加任何略過紀錄

## ADDED Requirements

### Requirement: Visible skip reasons for circuit-open profiles

router 在主迴圈遇到 circuit breaker 開啟、且 enabled 與 task class 相符的 profile 時，SHALL 略過執行並附加一筆 `failure_category="ineligible"`、零 elapsed、不消耗 agent call 的紀錄，原因 SHALL 標明 `circuit_open` 與開啟 circuit 的那次失敗類別。這類略過紀錄 MUST NOT 計入 `max_attempts`，MUST NOT 在已有真實失敗時成為 terminal attempt。當 session 沒有任何真實 attempt、所有 enabled profile 都被略過時，exhausted 錯誤 SHALL 維持 `ineligible` 類別（park 為 `backend_unavailable`），但錯誤訊息與 park 證據的 `attempts_detail` SHALL 逐一列出每個 profile 的略過原因，MUST NOT 為空。disabled 或 task class 不符的 profile 仍屬宣告式缺席，不產生紀錄。

#### Scenario: Whole chain circuit-open leaves per-profile reasons
- **WHEN** 同一 run 的前一個 session 讓所有 profile 在數秒內失敗，下一個 session 在 60 秒 cooldown 內開始
- **THEN** 該 session SHALL 以 `backend_unavailable` park，`attempts_detail` 對每個 enabled profile 各有一筆 `circuit_open` 原因，錯誤訊息列出每個 profile 的原因，且不呼叫任何 agent

#### Scenario: Circuit skips do not crowd out a healthy profile
- **WHEN** `max_attempts` 小於 circuit-open profile 數量，而 chain 後段還有健康的 profile
- **THEN** 健康的 profile SHALL 仍被嘗試，circuit 略過紀錄不佔用 attempt budget

### Requirement: Persistent backoff for deterministically failing profiles

dream／`hippo atomize`、title importer 與 `hippo retitle`、skillopt（rollout、judge、optimizer）的 router SHALL 共用同一個持久 profile 健康狀態，存放在 memory root 的 `runtime/agents/profile-health.json`，並 SHALL 依 router 的 task class 分開記錄，某一 task class 的結果 MUST NOT 清除或建立另一 task class 的狀態。各路徑可能同時更新：讀改寫 SHALL 以檔案鎖序列化，暫存檔名 SHALL 每次唯一，並發更新 MUST NOT 遺失或倒退退避計數。只有確定性失敗 SHALL 進入退避：nonzero exit、在 30 秒內失敗，且 sanitized stderr 開頭的有限視窗內以 CLI 參數解析錯誤（`invocation`）或憑證錯誤（`credential`）開頭；比對 MUST NOT 使用視窗之外的 stderr，以免 CLI 回顯的 prompt 內容觸發退避。timeout、invalid_output、quota、一般 process 失敗 MUST NOT 進入退避。`invocation` SHALL 一次即退避，`credential` SHALL 連續兩個 session 才退避；退避時間 SHALL 從 1 小時起跳、每次加倍、上限 24 小時，到期後允許一次探測。退避中的 profile SHALL 被略過並留下 `backoff <kind> until <UTC>` 的 ineligible 紀錄，不消耗 agent call、不計入 `max_attempts`。成功或非確定性結果 SHALL 清除狀態；狀態 SHALL 綁定 rendered command fingerprint，command 變更即不再套用。狀態讀寫失敗 MUST NOT 讓蒸餾失敗；dry-run MUST NOT 寫入狀態。`hippo doctor` SHALL 在對應 profile 顯示退避類別、到期時間與最後 stderr 摘要，且不因退避改變 exit code。

#### Scenario: Invocation failure is not retried by the next session
- **WHEN** 一個 profile 在 2 秒內以 `error: Invalid command format` exit 1，之後新的 dream run 建立新的 router
- **THEN** 下一個 session SHALL 不呼叫該 profile、直接由下一個 profile 接手，並在 provenance 留下 `backoff invocation until <UTC>` 的略過紀錄

#### Scenario: Echoed prompt text does not trigger backoff
- **WHEN** 某 CLI 把 prompt 回顯到 stderr，而 session 內容含有 `unknown option` 之類字樣，但 stderr 開頭是 CLI banner
- **THEN** 該失敗 MUST NOT 被判為確定性失敗，profile 不進入退避

#### Scenario: Doctor shows the backoff state
- **WHEN** operator 執行 `hippo doctor` 時某 profile 正處於退避
- **THEN** external agent profiles 段落 SHALL 在該 profile 後逐 task class 顯示 `health[<task_class>]=backoff(<kind>) until <UTC>` 與最後 stderr 摘要，doctor exit code 不因此改變

#### Scenario: Concurrent writers keep every update
- **WHEN** 多個 atomize／dream／importer／skillopt 行程同時記錄同一 profile 的確定性失敗
- **THEN** 最終的連續失敗次數 SHALL 等於所有行程記錄次數的總和，且不留下暫存檔

#### Scenario: Title and skillopt paths honor the same backoff
- **WHEN** title importer 或 skillopt rollout 的 router 遇到同一 task class 中處於退避的 profile
- **THEN** 該 profile SHALL 被略過而不呼叫外部 CLI，且 title 的成功 MUST NOT 清除 atomization 的退避

### Requirement: Claude profile output-contract system prompt

canonical `claude` profile 的 argv SHALL 以 `--system-prompt` 傳入任務中立的輸出契約，取代 Claude Code 預設（要求 Markdown 排版的）系統提示；契約 SHALL 要求只輸出 user message 指定格式的內容，MUST NOT 允許 Markdown、code fence、標題或前後說明文字，且 MUST NOT 寫死 JSON schema，讓 atomization、title、skillopt 共用。canonical 預設與出貨的 `atomizer.yaml` 模板 SHALL 逐 token 相同。response parser 的嚴格度 MUST NOT 因此放寬。

#### Scenario: Claude output stays a bare canonical document
- **WHEN** claude profile 蒸餾一個包含 code block 與 JSON 片段的 session
- **THEN** 輸出 SHALL 是不含 fence 或前後散文的單一 canonical JSON 物件，嚴格 parser 直接接受
