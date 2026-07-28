---
type: fix
---
- 實作 issue #80 的 Task 1 與 Task 2（未完成 Task 3）：
  - `run_session` 使用 `min(FIXED_SESSION_DEADLINE_CAP_SECONDS, max(FIXED_SESSION_DEADLINE_SECONDS, FIXED_PER_CHUNK_DEADLINE_SECONDS * chunk_count))` 計算 chain deadline；`FIXED_PER_CHUNK_DEADLINE_SECONDS=240`、`FIXED_SESSION_DEADLINE_CAP_SECONDS=1800` 已加入並保留 `FIXED_TIMEOUT_SECONDS=300` 不變，單次呼叫仍走 `min(profile.timeout, remaining_seconds)`。
  - `AgentProfile` 新增 `max_session_chunks` 驗證欄位（`None`/正整數），`eligible()` 支援 keyword-only `chunk_count`，當 `chunk_count > max_session_chunks` 回 `False, "session_size"`；router 以實際 chunk 數入呼叫 `eligible(...)`，超出宣告者會以 `ineligible` 留在 `attempts` 並不消耗 agent call。
  - `cache_namespace()` 的 profile 描述納入 `max_session_chunks`，但未改變 `command_fingerprint` 與 `cache_identity`；`claude`、`codex` 預設值在 `default_profiles()` 與 `atomizer.yaml` 均為 `6`，`cg` 為 `None`。
  - 先完成 `test_external_agent_profiles.py` 的紅綠迴路，更新 `test_external_agent_profiles.py`、`paulsha_hippo/agent_profiles.py`、`paulsha_hippo/atomizer/atomizer.yaml`。
- 未完成：Task 3 的分段保留/續跑與混合 profile provenance 還待後續交付。
