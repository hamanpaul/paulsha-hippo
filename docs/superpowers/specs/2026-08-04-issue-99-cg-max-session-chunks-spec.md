---
status: accepted
work_item: issue-99-cg-max-session-chunks
---

# cg profile 大 session 停損（max_session_chunks，issue #99）規格

## Problem and Outcome

大 session（>6 chunks）落到 cg profile 時，cg 會先燒 153.3 秒才以 exit 3 失敗（`hippo-copilot-core: no parseable JSON`），而非快速讓給下一個 profile。

生產實證：

- 原始事故（issue #99 body）：213KB/47+ fragments 的 session，tier-1（claude/codex）先因既有 `max_session_chunks`（7）判 ineligible 退出，cg 接手後燒滿 153.3 秒才吐不可解析輸出；同 session 續落 local-vllm 也因 `truncated at max_tokens`（issue #74 範疇）失敗，四 backend 全滅、session park，直接觸發 AR-11 soak 窗口重置。
- 2026-08-03 追加事故：792 fragments 的更大 session 再現同型崩潰——這次 claude／codex 連嘗試都沒有（`eligible()` 的 `session_size` gate 在 chunk 數超過各自 `max_session_chunks` 時直接判 ineligible），但 cg 仍照樣燒到 exit 3；local-vllm 仍是截斷失敗。四層全滅、window reset。

根本原因鎖定在 cg profile **沒有宣告** `max_session_chunks`——這個欄位與對應的 ineligible 路徑（`session_size` gate）已由 #85/#100 為 tier-1（claude/codex）建立並在生產驗證（tier-1 目前宣告上限 7）。cg 只是尚未套用同一機制。

預期結果：cg profile 宣告既有欄位 `max_session_chunks: 6`（依 issue 實證：6 chunks 內可解析、7+ 不可），超過上限的 session 對 cg 直接判 `ineligible`（reason `session_size`）、記錄 provenance 後立即讓給下一個 profile，不再實際嘗試呼叫、不再燒 153 秒才失敗。

## Goals

**本次交付範圍為停損版，不含根治：**

- G1（停損）：cg profile 在 `paulsha_hippo/agent_profiles.py::default_profiles()` 與 `paulsha_hippo/atomizer/atomizer.yaml` 的 cg block 兩處宣告 `max_session_chunks: 6`，逐 token 同步，沿用 #85/#100 已建立的 `session_size` ineligible 機制，不新增機制。
- G2：新增測試涵蓋「超過 6 chunks 的 session 對 cg 判 ineligible（reason `session_size`），不實際嘗試呼叫」與「chunk 數在 6 以內時 cg 行為不變」兩種情境。
- G3：出貨模板守門測試（`tests/test_external_agent_profiles.py::test_packaged_config_template_argv_matches_canonical_defaults`）綠——保證 `atomizer.yaml` 與 `default_profiles()` 不漂移。
- G4：全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠；新增 `changelog.d/99-cg-max-session-chunks.md`（type: fix）。

## Non-goals（根治不在本次）

- **不**量測或修改 copilot CLI 鏈路對大 payload 的 JSON 抽取／截斷行為——這需要對真實 copilot CLI 打大 payload 才能量測，非確定性、無法自動化驗證，明確留在 issue #99 追蹤、不在本 spec 範圍。
- **不**變更 tier-1（claude/codex）既有 `max_session_chunks: 7` 的數值或機制。
- **不**處理 local-vllm 的 `truncated at max_tokens`（屬 issue #74 範疇）。
- **不**新增新的 gate 欄位或新的 ineligible reason；完全重用 `session_size` 既有語意。

## Acceptance

- 對 chunk 數 > 6 的 session，`AgentProfile.eligible(chunk_count=...)` 對 cg 回傳 `(False, "session_size")`，且該 session 不再對 cg 產生實際呼叫（無 subprocess 執行、無 153 秒級耗時）。
- 對 chunk 數 ≤ 6 的 session，cg 的 eligible 判定與行為與本次變更前一致（零回歸）。
- `atomizer.yaml` 的 cg block 與 `default_profiles()` 的 cg row 逐 token 一致（守門測試綠）。
- 全套 pytest、policy_check、openspec strict 全綠。
