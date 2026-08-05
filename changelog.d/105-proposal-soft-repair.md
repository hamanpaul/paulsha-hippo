---
type: fix
---
- 修 issue #105：LLM 回應中單一 proposal 帶 schema 不認得的未知欄位（如 `tags2`）時，`atomizer/llm_output.py::_build_proposal` 原將其併入 hard violation 拋 `LlmOutputError`，整份回應（其餘 N 個合法 proposals）一起判死，fallback 鏈全滅 → session park。未知欄位改降級為 soft violation：決定性丟棄該欄位並記 warning（含 proposal 序號與被丟棄的欄位名，可觀測），其餘 proposal 正常產出。
- Hard violation 判死語意一條未鬆：缺 `title`、非法 `artifact_kind`、空 `body`、空 `source_fragment_indices` 等仍讓整份回應拋 `LlmOutputError`（防 retry 非決定性遺失 findings 的設計不變量），`_parse_proposals` 的不變量註解同步更新，明示 hard/soft 分界理由——未知欄位丟棄是決定性操作、不產生 retry。
- 測試補齊三面：soft-repair 後合法 proposals 存活、warning 內容可觀測（欄位名＋序號）、hard violation 逐條列舉仍整份判死。
