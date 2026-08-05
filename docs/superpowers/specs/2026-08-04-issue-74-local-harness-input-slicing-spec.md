---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# local-harness map-reduce 輸入切分（issue #74）規格

## Problem and Outcome

`contrib/local-harness/harness.py`（tier-3 `local-vllm` 保底）的兩段式 map-reduce 流程只切輸出、不切輸入：pass 1（enumerate）算出每個 concept 的 `fragment_indices`，但 pass 2（write）組 message 時仍把整份 `prompt`（含全部 fragment）逐字重送一遍，只在指令文字裡「告訴」模型該用哪些 index。

實測 `claude-code:e41dbdd3-…`（48 fragments／100,523 bytes）於三個 effort 全部失敗且失敗只是遷移、沒有消失：`low`（203s，`truncated at max_tokens`）、`medium`（357s，`truncated at max_tokens`）、`high`（400s，`vLLM request failed: timed out`）。`medium`／`high` 的耗時已遠超 `FIXED_TIMEOUT_SECONDS = 300` 的 per-agent 上限——在有界設計下，這個 payload 規模任何 effort 都不可能完成。

`ENUM_SCHEMA` 的 `fragment_indices` 是必填欄位（`required` 含之，且 `minItems: 1`），pass 1 已精確算出每個 concept 需要哪些 fragment，這項資訊在 pass 2 被完整丟棄。`paulsha_hippo/atomizer/prompt.py::build_prompt()` 以 `[fragment N]` 標記逐段輸出（`label = f"fragment {fragment.fragment_index}"`，多段 fragment 另加 `part X/Y` 後綴），故 harness 可自行依標記裁切輸入，不需要 hippo 端改動 prompt 契約。

預期結果：pass 2 每次 write 只送該 concept 的 `fragment_indices` 對應的 `[fragment N]` 區塊（含可選 ±1 鄰域）與必要的 preamble／`## Output` 指示區塊，不再重送全量 payload；單次輸入 bytes 顯著小於原文；同一 48-fragment payload 有機會落回 per-agent 300s 上限內完成而不觸發 `truncated at max_tokens`；裁切邏輯失敗時 fail-safe 退回全量重送並記 warning，不得讓 write 直接失敗。

## Goals

- G1：新增純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`，依 `fragment_indices` 從完整 prompt 裁出對應 `[fragment N]` 區塊（±1 鄰域），保留 preamble（skill/known projects/session project 區塊）與 `## Output` 指示區塊。
- G2：pass 2 write 呼叫點改用裁切後的 prompt 取代目前的全量 `prompt`。
- G3：裁切失敗（正則抓不到標記、indices 越界、空集合等任何非預期狀況）fail-safe 退回全量 prompt 並記 stderr warning，不得讓該 concept 的 write 直接失敗。
- G4：新增測試涵蓋正常裁切正確性（含 bytes 顯著縮減）與邊界情境（越界／空集合／單 fragment prompt）。

## Non-goals

- 不改 `paulsha_hippo/atomizer/prompt.py` 的 prompt 契約或 `[fragment N]` 標記格式。
- 不改 pass 1（enumerate）邏輯或 `ENUM_SCHEMA`。
- 不解決「token 開銷是否來自 guided decoding 本身」——issue 已誠實列出這點無法只靠切輸入解釋，需另行量測，非本次範圍。
- 不做端到端「真實 local-vllm 對 48-fragment payload 實測不再 truncated」驗證——此為 merge 後 operator 手動驗證項（見 accepted plan）。
- 不影響 hippo core／package／CI：`contrib/local-harness/` 不進 wheel、不被 package 引用，僅版控與部署來源。

## Acceptance

- 合成 8 個 `[fragment N]` 區塊的 prompt，`indices={3}` 時 `slice_prompt_by_fragments` 輸出僅含 fragment 2/3/4（±1 鄰域）與 preamble、`## Output` 區塊，bytes 顯著小於原文。
- indices 越界、空集合、單 fragment prompt 等邊界情境下裁切失敗，函式退回全量 prompt 並記 warning，呼叫方不拋例外。
- pass 2 write 實際呼叫點使用裁切後的 prompt。
- 全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
- 端到端 48-fragment 真實 local-vllm 驗證留待 merge 後 operator 於有 local-vllm 端點的環境手動執行（非本次驗收項）。
