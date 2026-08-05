---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# Issue #74 local-harness map-reduce 輸入切分設計

- 日期：2026-08-04
- Issue：[#74](https://github.com/hamanpaul/paulsha-hippo/issues/74)
- 狀態：已核可，待實作

## 背景

證據與根因見 spec（`docs/superpowers/specs/2026-08-04-issue-74-local-harness-input-slicing-spec.md`）與同名 design。`prompt` 全文由 `paulsha_hippo/atomizer/prompt.py::build_prompt()` 產出，結構固定：skill 文字 → `## Known projects` → （可選）`## This session's project` → `## Session fragments to atomize`（逐 fragment 輸出 `[fragment N]` 標記，多段 fragment 另加 `part X/Y` 後綴）→ `## Output`。修改點集中在 `contrib/local-harness/harness.py` pass 2 write（約 L408-436）。

## Decisions

### D1：新增純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`

依 concept 的 `fragment_indices` 從完整 prompt 裁出對應 `[fragment N]` 區塊（±1 鄰域），保留 preamble（skill／known projects／session project 區塊）與 `## Output` 指示區塊。同一 `fragment_index` 若因 `part_count > 1` 而拆成多個 `[fragment N part X/Y]` 標記，整組視為同一索引、整組保留或整組捨棄，不拆散單一 fragment 的分段。純函式（字串輸入輸出、不吃 harness 其他狀態）便於合成 8-fragment fixture 直接單元測試，也不需要改動 `build_prompt()` 的輸出格式或契約。

### D2：Fail-safe——裁切失敗退回全量 prompt 並記 warning

裁切函式偵測到任何不可信狀況（抓不到 `[fragment N]` 標記、`indices` 全部越界、擴張後索引集合為空等）一律回傳原始 `prompt` 全文，呼叫方寫一行 stderr warning 後照舊送出全量 payload，不得讓該 concept 的 write 直接失敗。理由：`local-vllm` 是 tier-3 保底，本次是效能最佳化而非功能新增，任何裁切邏輯的 bug 都不應該把「慢但能跑」變成「跑不了」；fail-safe 使最壞情況等價於變更前行為。

### D3：Contrib-only 風險邊界，不進 wheel

本次變更全數落在 `contrib/local-harness/`（`harness.py` 本體＋新測試檔），不觸碰 `paulsha_hippo/` 套件程式碼、不改 `paulsha_hippo/atomizer/prompt.py` 的輸出契約、不改其他 tier profile 的呼叫路徑。`contrib/local-harness/` 明載為「NOT part of the paulsha-hippo repo/wheel」，改壞不影響 hippo core、其他 profile 或 CI 的套件測試，爆炸半徑限定在 `local-vllm` 這一條 tier-3 保底路徑。若實作時發現裁切需要跨到 `paulsha_hippo/` 才能可靠取得 fragment 邊界（例如需要 `Fragment` 物件而非純文字正則），視為超出本次範圍，應於 PR/issue 說明並重新評估邊界。

## Testing

- 新增：8-fragment 合成 prompt，`indices={3}` 時輸出僅含 fragment 2/3/4 與 preamble、`## Output` 區塊，bytes 顯著小於原文。
- 新增：indices 越界／空集合／單 fragment prompt 的邊界行為——裁切失敗退回全量並記 warning，不拋例外。
- 新增：pass 2 write 呼叫點整合行為（送出的 payload content 已裁切）。
- 既有：全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
- 端到端（真實 local-vllm 48-fragment 實測）留待 merge 後 operator 手動驗證，非本次自動化範圍。
