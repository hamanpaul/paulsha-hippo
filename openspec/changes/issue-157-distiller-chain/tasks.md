---
status: accepted
work_item: issue-157-distiller-chain
---

# Issue #157 蒸餾鏈三段式浪費 tasks

## Task 1：circuit-open profile 的略過原因

- [x] 先寫測試：同一 router 第二個 session 在所有 circuit 皆開時，attempts 逐一帶 `circuit_open` 原因；pipeline park 證據的 `attempts_detail` 不為空（main 上 RED：`[]`）。
- [x] 先寫測試：略過紀錄不佔 `max_attempts`；真實失敗後的略過紀錄不改變 raise 的 category／profile。
- [x] 實作：router 主迴圈與 chain-budget 中斷路徑記錄 circuit 原因；全部略過時以最後一筆略過紀錄作為 terminal。
- [x] 既有 #106 circuit-open 測試改為驗證新的略過原因（不再省略、也不記成 `session_deadline`）。

## Task 2：確定性必敗 profile 的持久退避

- [x] 先寫測試：cg 形狀的 `Invalid command format` 失敗寫入狀態檔，新 router 實例下一個 session 不再呼叫 cg 並留下 `backoff invocation until` 紀錄（main 上 RED：模組不存在）。
- [x] 先寫測試：到期探測與加倍、上限、成功清除、非確定性失敗不退避、憑證兩次門檻、command 變更失效、毀損檔、唯讀、doctor 顯示。
- [x] 實作 `paulsha_hippo/agent_health.py`，router 接 `health`，atomize／dream 的 promoter 建構接上 store（dry-run 唯讀），doctor 顯示狀態。

## Task 3：claude 輸出契約系統提示

- [x] 先寫測試：預設 claude argv 含 `--system-prompt DISTILLER_SYSTEM_PROMPT`，且通過 argv 安全驗證（main 上 RED）。
- [x] 實作：`default_profiles()` 與 `atomizer.yaml` 同步；以合成 session 實測（main argv 4/6 失敗 → 加上後 8/8 + 真實 router 路徑 3/3 合法）。

## Task 4：cg launcher prompt 綁定

- [x] 先寫測試：假 copilot 記錄 argv，以 `---` 開頭的 prompt 必須是單一 `--prompt=` token（main 上 RED：`Invalid command format`）。
- [x] 實作：`hippo-copilot-headless-core` 改用 `--prompt="$prompt"`。

## Task 5：PR #162 審查修正

- [x] 先寫測試：4 個行程並發更新同一 profile，最終計數必須是 120（前一版實作 RED：只剩 30）；佔住舊的固定暫存檔名時仍寫得進去（RED：狀態沒寫入）。
- [x] 實作：讀改寫以 `profile-health.json.lock` 的 flock 序列化，暫存檔名每次唯一。
- [x] 先寫測試：title importer、`hippo retitle`、skillopt 三個 router 讀寫同一個 store；task class 互不影響（RED：title 沒有狀態、skillopt router 沒有 `health`）。
- [x] 實作：狀態依 task class 分開；title 以 ContextVar 傳 memory root；skillopt 依 `--dry-run` 決定唯讀；doctor 逐 task class 顯示。

## Task 6：PR #162 第二輪審查修正

- [x] 先寫測試：失敗寫入者持鎖寫入途中，另一行程記錄成功——成功必須清除退避（RED：鎖外讀到舊狀態就放棄，退避殘留）。
- [x] 先寫測試：另一行程持鎖不放時 `record_outcome()` 與 router 在期限內返回、寫 log、讀取不卡住（RED：無期限阻塞）。
- [x] 實作：移除「成功且鎖外看起來沒有狀態」的快速路徑，一律鎖內重讀；取鎖改 `LOCK_NB` 輪詢加 2 秒期限，逾時 fail-open 並暫停更新 60 秒。

## Task 7：文件與收尾

- [x] `docs/backend-matrix.md`、`contrib/local-harness/README.md` 同步；`changelog.d/157-distiller-chain.md` 並鏡像 `CHANGELOG.md [Unreleased]`。
- [x] 全套測試、`openspec validate --all --strict`、`policy_check` 通過。
