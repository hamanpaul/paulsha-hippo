---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

## Why

修好 tier-1 的輸出契約（#77）之後，**有真實 ingress 的 dream timer cycle 仍然產不出任何 accepted atom**。`2026-07-28T07:00:42Z` 那輪是修復後第一個有 ingress 的 cycle：`split_sessions=1`、`slices=0`、`status=partial`，session 被 park；parked evidence 顯示 claude 跑了 299.7s 後 `timeout`（`stdout_bytes=0`，最後一次呼叫只剩 2 秒），codex 接手只剩 1 秒。

根因是算術：claude 單 chunk 實測 190.5s（另次 244s），47 fragments／212,950 bytes 的 session 打包成 **7 chunks**，7 × 190s ≈ 1,330s，而整條 fallback 鏈的預算固定 600s。router 給每次呼叫 `min(profile.timeout, remaining_seconds)`，因此 tier-1 跑到第二、三個 chunk 就把整條鏈的時間用光。

第二層問題讓浪費加倍：現行契約要求「後續 profile 從 frozen input 完整重跑整個 session」，於是 claude 在那 299.7s 內**已經產出且已驗證通過**的前段 chunk 全被丟棄，codex 拿著剩下的 1 秒從 chunk 0 重來。

後果是 AR-11 soak 結構性無法累積：沒有 ingress 的 cycle 不計分，有 ingress 的 cycle 因 session 過大而必然 park、同樣不計分。

## What Changes

- **session 預算隨 chunk 數縮放**：有效預算改為 `min(1800, max(600, 240 × chunk 數))`。600s 成為下限（單／雙 chunk 的小 session 行為不變）、240s 取自實測單 chunk 上緣、1800s 上限確保單一 session 不吃掉每小時 timer 視窗的一半以上。**單次呼叫上限 `FIXED_TIMEOUT_SECONDS = 300` 完全不動**，hang 防護不受影響。
- **profile 宣告適用範圍**：`AgentProfile` 新增 `max_session_chunks`（預設 `None` = 不限）。超出宣告範圍的 profile 在該 session 判為 ineligible（沿用既有 `ineligible` 類別，不新增 fallback category），記入 attempt provenance 但**消耗零個 agent call**，讓路給能在預算內完成的 profile。
- **已驗證的 chunk 成果跨 profile 保留**：後續 profile 從第一個**未驗證**的 chunk 續跑，而非從 chunk 0 完整重跑。這修改了「restart the complete session from frozen input」這條既有契約，因此需要本次的 spec delta。frozen prompt 序列與 per-chunk 驗證不變；耗盡時仍 park、仍不做 partial publication。
- **provenance 誠實化**：一個 session 由多個 profile 分段完成時，逐 chunk 記錄實際產出它的 profile，session 層沿用既有的 `degraded-success`。禁止把混合 profile 的 session 記成單一 profile 產出。

## Capabilities

### Modified Capabilities

- `stage2-llm-distillation`：
  - 「Deterministic tiered fallback」的全域預算由固定值改為隨 chunk 數縮放的有界函式；fallback 的重跑語意由「完整重跑整個 session」改為「從第一個未驗證的 chunk 續跑」；新增以宣告式 `max_session_chunks` 判定 profile 對該 session 是否適用的 ineligible 條件。
  - 「Profile-bound cache and attempt provenance」擴充為：session 由多個 profile 分段完成時，provenance 必須能逐 chunk 指出實際產出者。

## Non-Goals

- 不調整 `FIXED_TIMEOUT_SECONDS`（單次呼叫 300s）；放寬它等於放棄 hang 防護。
- 不改變 chunk 打包演算法（`budget.pack_prompt_chunks` 的 token／bytes 上限）。
- 不處理 #74（`local-vllm` map-reduce 只切輸出不切輸入）。
- 不改變耗盡時的行為：仍 park 一次、仍不做 partial publication。
