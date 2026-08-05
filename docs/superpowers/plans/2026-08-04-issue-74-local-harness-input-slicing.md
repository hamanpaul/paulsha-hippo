---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# local-harness map-reduce 輸入切分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試再實作。

**Goal:** `contrib/local-harness` 的 map-reduce 第二階段（per-concept write）不再每次重送全量 payload——依 concept 的 `fragment_indices` 裁切輸入（±1 鄰域），大 session（實測 48 fragments/100KB）的單次呼叫 payload 縮到與 concept 相稱的大小。

**Root cause 位置：** `contrib/local-harness/harness.py` pass 2 write（約 L408-436）只切輸出、不切輸入。

**風險註記：** contrib-only，不進 wheel、不被 package 引用，改壞不影響 core／CI。但 fragment-marker 正則切分要準確（`[fragment N]` 標記），preamble 與 `## Output` 指示區塊必須保留。

**Target branch:** `feature/74-harness-input-slicing`

## Global Constraints

- 語言 zh-tw；TDD 先 RED 再實作。
- 交付治理：`changelog.d/<slug>.md`、pytest／policy_check／openspec strict 全綠、PR checklist 全勾。body 註 `Refs #74`＋說明：**端到端驗證（真實 local-vllm 對 48-fragment payload 實測不再 `truncated at max_tokens`）屬手動驗證項，merge 後由 operator 在有 local-vllm 端點的環境執行**，故不用 Closes（或如 maintainer 判定可關則改 Closes——由 review 階段決定）。
- contrib 沒有既有測試檔：新建測試放 `contrib/local-harness/tests/`（若該路徑不被 root pytest 收集，於 PR body 說明執行方式）或依 repo 慣例放 `tests/`。
- 全套測試在非巢狀 sibling worktree 跑。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。

## Task 1: 純函式輸入裁切

**檔案**：`contrib/local-harness/harness.py`、新測試檔

- [ ] 先寫測試：新純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`——合成含 8 個 `[fragment N]` 區塊的 prompt，`indices={3}` 時輸出僅含 fragment 2/3/4 與 preamble、`## Output` 區塊；bytes 顯著小於原文。
- [ ] 先寫測試：indices 越界／空集合／單 fragment prompt 的邊界行為（fail-safe：裁切失敗時退回全量並記 warning，不得讓 write 直接失敗）。
- [ ] 實作純函式並接進 pass 2 write 呼叫點。
- [ ] 全套綠；`changelog.d/74-harness-input-slicing.md`（type: fix）。
