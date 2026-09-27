---
status: accepted
work_item: issue-157-distiller-chain
---

## Why

2026-09-26 對部署中的 0.1.1 做 KPI 稽核，蒸餾鏈有三段式浪費：tier-1 第一順位的 cg 每次 1.9 秒必敗、claude 常在跑完整段生成後才被判 `invalid_output`、全部 profile 被判不可用時失敗紀錄的 `attempts_detail` 是空的。以 main 重現後，三者都仍存在：

1. cg 必敗的根因是 launcher 以 `-p "$prompt"` 傳 prompt；atomize prompt 一律以 skill frontmatter `---` 開頭，copilot CLI 1.0.88 把它當成旗標而回 `Invalid command format`。router 只有 60 秒的 in-memory circuit，每一輪 dream 都重建，所以必敗 profile 每一輪都被先試一次，`hippo doctor` 也看不到。
2. claude 的 `invalid_output` 根因是 Claude Code 預設系統提示要求 Markdown 排版，壓過 prompt 的「不得有 fence／前後散文」契約：以 main 的 prompt 組裝與 argv 實測 6 次中 4 次輸出被包進 ```json fence 或前置散文。issue 的「約 11 分鐘」是 attempt 層級的 elapsed，包含已驗證並保留下來的 chunk；真正浪費的是失敗那一個 chunk 的生成時間。
3. 空的 `attempts_detail` 來自 router 對 circuit-open profile 的靜默 `continue`：同一 run 的第一個 session 把整條鏈打穿後，60 秒內後續每個 session 都以零紀錄 park 成 `backend_unavailable`。

## What Changes

- router 對 circuit-open 的 profile 留下 `ineligible` 略過紀錄（`circuit_open after <category>`），不消耗 agent call、不佔 `max_attempts`，也不改變真實失敗後的 park 分類；全部略過時 exhausted 錯誤與 park 證據逐一列出原因。
- 新增 `paulsha_hippo.agent_health`：確定性必敗（啟動即失敗且 stderr 開頭是 CLI 參數解析或憑證錯誤）寫入 `runtime/agents/profile-health.json` 並指數退避；退避中的 profile 以 `backoff <kind> until <UTC>` 略過，`hippo doctor` 顯示狀態。
- claude profile argv 加 `--system-prompt <輸出契約>`，取代 Claude Code 預設系統提示；`atomizer.yaml` 模板同步。
- `contrib/local-harness` 的 copilot launcher 改用 `--prompt=<值>` 綁定 prompt。

不改變蒸餾結果語意、response parser 的嚴格度與 promotion 規則；不處理 #99。

## Capabilities

### Modified Capabilities

- `stage2-llm-distillation`：circuit-open profile 的略過紀錄、確定性失敗的持久退避、claude profile 的輸出契約系統提示。
