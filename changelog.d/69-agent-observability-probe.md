### Added

- parked evidence（`runtime/queue/_failed/*.json`）新增 `attempts_detail`：逐一 external agent fallback 嘗試的 `profile_id`／`exit_code`／`duration_seconds`／`stderr_tail`（既有 secret-redaction 淨化＋≤2000 字元截斷）／`stdout_bytes`／`failure_kind`（`timeout`／`nonzero_exit`／`empty_output`／`invalid_output`／`decode_error`），讓 timeout、CLI 靜默失敗、rate limit、進程被殺不再無法區分；既有欄位皆不變。

### Fixed

- `hippo doctor --probe-profiles`／`--fix-backend` 的 smoke probe 改為要求單一 JSON 回覆，驗證條件比照 atomize 既有的嚴格解析（`llm_output.parse_single_json_value`）——不再只驗「exit 0 + 非空」，修正與真實 atomize 結果反相關的誤判（守 JSON 契約但 prompt 未觸發 JSON 的 backend 誤判 FAIL；能回話但不守契約的 backend 誤判 ✓）。
