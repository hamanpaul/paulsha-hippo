### Fixed
- `contrib/local-harness`（#56 review）：harness.py「no model」錯誤訊息更正為 `HIPPO_LOCAL_VLLM_MODEL`（env 遷移殘留）；`--effort` 非法值改 fail-loud（不再默默 fallback 到 low 隱藏設定錯誤）；copilot-chain launcher 以受限 KEY=VALUE parser 取代 `source`（消除 tampered env file 的任意 shell 執行風險，僅保留 `$NAME`/`${NAME}` 受控展開）。
