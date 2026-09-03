---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# Issue #74 local-harness map-reduce 輸入切分提案

## Why

`contrib/local-harness/harness.py`（tier-3 `local-vllm` 保底）的兩段式 map-reduce 只切輸出、不切輸入：pass 2（write）每次都把整份 prompt（含全部 fragment）重送一遍，只在指令文字裡「告訴」模型該用哪些 `fragment_indices`。實測 48-fragment／100,523 bytes 的 session 於三個 effort 全部失敗（`low`/`medium` `truncated at max_tokens`、`high` `vLLM request failed: timed out`），`medium`/`high` 耗時已遠超 `FIXED_TIMEOUT_SECONDS = 300` 的 per-agent 上限——在有界設計下，這個 payload 規模任何 effort 都不可能完成。

裁切輸入所需的資訊早已存在且被丟棄：pass 1 的 `ENUM_SCHEMA` 中 `fragment_indices` 為必填欄位，且 `paulsha_hippo/atomizer/prompt.py::build_prompt()` 已以 `[fragment N]` 標記逐段輸出，harness 可自行依標記裁切輸入，不需要 hippo 端改動 prompt 契約。

## What Changes

- 新增純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`：依 concept 的 `fragment_indices` 從完整 prompt 裁出對應 `[fragment N]` 區塊（±1 鄰域），保留 preamble 與 `## Output` 指示區塊。
- pass 2 write 呼叫點（`contrib/local-harness/harness.py` 約 L408-436）接上此函式，取代目前每次重送的全量 `prompt`。
- Fail-safe：裁切失敗（正則抓不到標記、indices 越界、空集合等）退回全量 prompt 並記 stderr warning，不得讓 write 直接失敗。
- 新增測試：合成 8-fragment fixture 驗證裁切正確性與 bytes 顯著縮減；邊界情境（越界／空集合／單 fragment prompt）。
- 新增 `changelog.d/74-harness-input-slicing.md`（type: fix，於實作 PR 提交，非本 define 套件範圍）。

## Impact

- 影響範圍：僅 `contrib/local-harness/harness.py` 與其新測試檔；不改 `paulsha_hippo/` 套件程式碼、不改 `paulsha_hippo/atomizer/prompt.py` 的輸出契約、不改其他 tier profile（`claude`/`codex`/`agy`/`cg`）的呼叫路徑。
- 風險：`contrib/local-harness/` 不進 wheel、不被 package 引用（僅版控與部署來源），改壞不影響 hippo core／CI；唯一風險是 `[fragment N]` 正則切分不準確或把 preamble／`## Output` 區塊誤裁掉，已以 fail-safe（D2）與合成 fixture 測試（D1）收斂。
- 端到端驗證（真實 local-vllm 對 48-fragment payload 實測不再 `truncated at max_tokens`）屬 merge 後 operator 手動驗證項，於有 local-vllm 端點的環境執行，非本次自動化驗收範圍。
- Authority：`docs/superpowers/plans/2026-08-04-issue-74-local-harness-input-slicing.md`、spec/design 同名對。
