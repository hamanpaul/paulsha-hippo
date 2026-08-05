---
status: accepted
work_item: issue-99-cg-max-session-chunks
---

## Why

大 session（>6 chunks）不再讓 cg 燒 153 秒才輸出不可解析 JSON 失敗——cg profile 宣告既有欄位 `max_session_chunks: 6`，超限即記 `ineligible` provenance、立即讓給下一個 profile。

## What Changes

- cg profile 宣告 `max_session_chunks: 6`（`paulsha_hippo/agent_profiles.py` 與 `paulsha_hippo/atomizer/atomizer.yaml`）。
- 6 chunks 內 cg 可正常調用，7+ chunks 即記 `ineligible` provenance 並跳過。

## Capabilities

### Modified Capabilities

- `stage2-llm-distillation`：cg profile 宣告 max_session_chunks: 6。
