---
status: accepted
work_item: issue-146-task-memory-payload
---

## Why

cross-session task memory 需要一個工具中立、可版本化的 payload 契約，能同時表達任務意圖、候選可用性、授權結果與實際內容交付結果；inline context 不能再被誤判成 read，且 shareable artifact 不得外洩 secret 或 denied note 內容。

## What Changes

- 新增 `paulsha_hippo.task_memory_payload`，集中處理 task memory payload envelope 的建構與驗證。
- payload 僅保留最多三筆已授權候選，並以 deterministic ordering 排序；候選摘要/摘錄走 shareable-safe redaction。
- 新增 delivery outcome summarizer，將 `inline`、`snapshot`、`note_fetch`、`ineligible`、`failure` 與 read KPI 分離；只有 `returned` 才算 read，且需 `applied` 才算 applied。

## Capabilities

### Added Capabilities

- `stage2-memory-task-payload`：版本化 payload envelope、capability-aware delivery outcome，以及 tool-neutral evidence/read KPI semantics。
