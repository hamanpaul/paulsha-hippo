---
status: accepted
work_item: issue-74-local-harness-input-slicing
---

# local-harness map-reduce 輸入切分（issue #74）設計

- 日期：2026-08-04
- Issue：[#74](https://github.com/hamanpaul/paulsha-hippo/issues/74)
- 狀態：已核可，待實作

## 背景

證據與根因見 spec。修改點集中在 `contrib/local-harness/harness.py` pass 2 write（約 L408-436）；`prompt` 全文以 `paulsha_hippo/atomizer/prompt.py::build_prompt()` 產出，結構固定為：skill 文字 → `## Known projects` → （可選）`## This session's project` → `## Session fragments to atomize`（逐 fragment 輸出 `[fragment N]` 標記＋內文，多段 fragment 另加 `part X/Y` 後綴）→ `## Output`（輸出指示）。本設計要解的問題是「切輸入的邊界怎麼劃、失敗怎麼收」。

## Decisions

### D1：新增純函式 `slice_prompt_by_fragments(prompt, indices, neighbor=1)`

簽名：`slice_prompt_by_fragments(prompt: str, indices: list[int], neighbor: int = 1) -> str`。

行為：
- 用正則（比照 issue 中已驗證的 `^\[fragment \d+` 前綴，錨定行首）掃出 `## Session fragments to atomize` 區段內每個 `[fragment N ...]` 標記的起訖位置，切出「該標記行＋其後內容直到下一個標記或區段結尾」作為一個 fragment block；同一 `fragment_index` 若有多個 `part X/Y` 標記（同一 fragment 被拆成多段），視為同一個索引下的連續 block，裁切時整組保留或整組捨棄，不拆散單一 fragment 的分段。
- 依 `indices` 集合擴張 ±`neighbor`（預設 1）個相鄰 fragment index，再依擴張後的索引集合挑出對應 block，依原順序重新組裝。
- 保留 `## Session fragments to atomize` 之前的全部 preamble（skill 文字／`## Known projects`／`## This session's project`）與 `## Output` 之後的全部輸出指示區塊——這兩段不受裁切影響，逐字保留。
- 選純函式（不是 method、不吃 harness 其他狀態）：輸入輸出都是字串／整數集合，最小依賴、最容易寫合成 fixture 測試（8-fragment 合成 prompt），也不需要改動 `paulsha_hippo/atomizer/prompt.py` 的輸出格式。

候選但不選：(a) 讓 `build_prompt()` 直接產生分段結構（如 list of dict）取代目前的純字串 prompt——會改變 prompt 契約，影響到非 local-harness 的呼叫者（其他 profile 走同一份全量 prompt 字串），風險與範圍都超出本 issue；(b) 在 harness 內用 JSON/結構化方式重新序列化 fragment——同樣不必要地擴大改動面。純字串裁切、原地替換是最小改動。

### D2：Fail-safe——裁切失敗一律退回全量 prompt 並記 warning

裁切函式內部任何非預期狀況（正則抓不到任何 `[fragment N]` 標記、`indices` 全部越界、擴張後索引集合為空、單一 fragment 的 prompt 裁完後與全量幾乎等長而失去意義等）一律視為「裁切不可信」，函式回傳原始 `prompt` 全文（不裁切），呼叫方（pass 2 write 迴圈）在偵測到「輸出等於輸入」或函式內部顯式回傳全量旗標時，寫一行 stderr warning（`hippo-local-harness: slice_prompt_by_fragments fallback: <reason>`），繼續照舊送出全量 payload 完成該 concept 的 write。

理由：issue 本文明載「裁切失敗時退回全量並記 warning，不得讓 write 直接失敗」——`local-vllm` 是 tier-3 保底，任何本次新增的裁切邏輯本身造成 write 失敗，都是比「效能未達預期」更嚴重的回歸（從「慢但能跑」變成「跑不了」）。fail-safe 使本次變更在最壞情況下等價於變更前的行為（全量重送），只在裁切可信時才拿到效能收益，不存在「裁切邏輯有 bug 導致核心功能壞掉」的下行風險。

不選「裁切失敗直接拋例外／die()」：會把一個純粹的效能最佳化，變成一個可能讓 write 整段失敗的新故障點，與 issue 的驗收語意（「不得讓 write 直接失敗」）直接衝突。

### D3：Contrib-only 風險邊界——不進 wheel、不被 package 引用

`contrib/local-harness/` 明載於檔案頭註解為「Local-only deployment artifact (NOT part of the paulsha-hippo repo/wheel)」，本次變更全數落在這個目錄內（`harness.py` 本體＋新測試檔），不觸碰 `paulsha_hippo/` 套件程式碼、不改 `paulsha_hippo/atomizer/prompt.py` 的輸出契約、不改其他 external-agent profile 的呼叫路徑。因此：
- 本次變更改壞不影響 hippo core、其他 tier profile（`claude`/`codex`/`agy`/`cg`）或 CI 的套件測試；爆炸半徑限定在 `local-vllm` 這一條 tier-3 保底路徑。
- 對應地，本次不要求也不應該去改動 `openspec/specs/stage2-llm-distillation/spec.md` 的既有 base spec 內容（如「Bounded zero-tool distillation」等既有 Requirement）——那些描述的是 hippo 核心的 chunk 打包／context 預算，屬另一層級；本次只在該 scope 下新增一個聚焦 `local-harness` 自身 pass 2 行為的 Requirement（見 openspec change 的 `specs/stage2-llm-distillation/spec.md`）。
- 若實作時發現裁切需要跨到 `paulsha_hippo/` 才能可靠取得 fragment 邊界資訊（例如需要 `Fragment` 物件而非純文字正則），視為超出本次 contrib-only 範圍，應回頭在 PR/issue 說明並重新評估邊界，而非默默擴大改動面。

## Testing

- 新增：8-fragment 合成 prompt，`indices={3}` 時 `slice_prompt_by_fragments` 輸出僅含 fragment 2/3/4 與 preamble、`## Output` 區塊，bytes 顯著小於原文。
- 新增：indices 越界／空集合／單 fragment prompt 的邊界行為——裁切失敗時退回全量並記 warning，函式不拋例外，呼叫方 write 不因此失敗。
- 新增：pass 2 write 呼叫點接上裁切函式後的整合行為（可用 mock/monkeypatch 驗證送出的 payload content 已裁切）。
- 既有：全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
- 端到端（真實 local-vllm 對 48-fragment payload 實測不再 `truncated at max_tokens`）不在本次自動化測試範圍——屬 merge 後 operator 手動驗證項。
