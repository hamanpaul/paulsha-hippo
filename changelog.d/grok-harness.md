---
type: feat
---
- 新增 Grok host：SessionStart 以頂層 `systemMessage` 注入 wake-up 與顯式 recall 指引（不接 UserPromptSubmit）；SessionEnd／PreCompact 截取 session，子 session 略過；PostToolUse 只對 `read_file`／`hashline_read` 記 read attribution 且不印 stdout。`importer/adapters/grok.py` 讀 `chat_history.jsonl`。消費送達尚未以 MEMO-TOKEN 實測，capability matrix 總評維持 inconclusive。
- `custom-skills/add-new-harness/`：把新 harness 接進 hippo 時要對齊的契約（stdout 形狀、SessionEnd 而非 Stop、capability matrix 誠實分級）。
