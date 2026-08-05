---
status: accepted
work_item: issue-99-cg-max-session-chunks
---

# cg profile 大 session 停損（max_session_chunks）tasks

## Task 1: cg 宣告 max_session_chunks

- [x] 先寫測試：比照既有 tier-1 `max_session_chunks` 測試模式，構造 chunk 數超限的 session，斷言 cg 被記 `ineligible`（provenance record 存在、reason 正確）而非實際嘗試呼叫。
- [x] 先寫測試：chunk 數在限內時 cg 行為不變。
- [x] 實作：cg profile 加 `max_session_chunks: 6`（依 issue 實證：6 chunks 內可解析、7+ 不可）；`atomizer.yaml` 與 `default_profiles()` 兩處同步。
- [x] 模板同步測試綠；全套綠；`changelog.d/99-cg-max-session-chunks.md`（type: fix）。
