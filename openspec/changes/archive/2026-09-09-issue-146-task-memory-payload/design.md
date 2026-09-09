---
status: accepted
work_item: issue-146-task-memory-payload
---

# Issue #146 task memory payload 設計

## Background

既有 memory retrieval 與 read attribution 已能做 shortlist offer 與 read ledger，但缺少一個可被 host adapter 重用的正式 payload 契約：候選上限、授權過濾、shareable redaction，以及 inline/snapshot/note_fetch 的 read 語意原本散落或不存在。這張卡先以最小 diff 補純函式模組，不改現行 host routing。

## Decisions

### D1：以獨立純函式模組承接 payload contract

`paulsha_hippo.task_memory_payload` 只負責建構/驗證 envelope 與彙整 delivery outcome，不直接做 IO、provider 呼叫或授權繞過，讓 Cortex #857 可作 thin adapter 映射。

### D2：候選固定上限 3、只保留已授權項目

builder 先依 `rank` 與 `ref` 做 deterministic ordering，再裁成最多三筆；未授權候選直接排除，provider unavailable 但已授權的候選可保留 availability failure facts。

### D3：shareable-safe redaction 先於持久化/展示

候選 `summary`/`excerpt` 先去掉 home path 再走既有 `redact_secret_text()`，避免 public fixture、report 或 changelog 不小心持久化敏感片段。

### D4：returned 才能進 read，returned+applied 才能進 applied

delivery summarizer 將 mode 與事件拆開判定：`inline`/`snapshot` 僅代表內容已隨 payload 或 artifact ready，不得推導 read；`note_fetch` 只有出現 `returned` 才算 read，且必須再看到 `applied` 才算 applied。`failed` 僅保留分類，不得升格成功。

## Testing

- `tests/test_task_memory_payload_contract.py`
- `python3 -m pytest tests/test_task_memory_payload_contract.py -q`
- `python3 -m pytest tests/ -q`
- `python3 -m policy_check --repo .`
- `openspec validate --all --strict`
