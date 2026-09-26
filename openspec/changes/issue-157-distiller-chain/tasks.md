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

## Task 5：文件與收尾

- [x] `docs/backend-matrix.md`、`contrib/local-harness/README.md` 同步；`changelog.d/157-distiller-chain.md` 並鏡像 `CHANGELOG.md [Unreleased]`。
- [x] 全套測試、`openspec validate --all --strict`、`policy_check` 通過。
