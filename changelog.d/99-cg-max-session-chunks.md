---
type: fix
---
- 修 issue #99：`cg` external-agent profile 宣告 `max_session_chunks: 6`，讓 7+ chunk 的大 session 在 profile eligibility 階段直接記 `ineligible`／`session_size` provenance 並交給下一個 fallback profile，而不是先花一輪 cg 呼叫才以不可解析 JSON 失敗。`default_profiles()` 與出貨模板 `paulsha_hippo/atomizer/atomizer.yaml` 兩處同步，避免模板漂移把停損配置漏出貨。
