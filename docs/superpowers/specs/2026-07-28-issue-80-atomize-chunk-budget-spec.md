---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# 大 session 的 chunk 序列預算與部分成果保留（issue #80）規格

## Problem and Outcome

#77 修好 tier-1 `claude` profile 的輸出契約之後，**有真實 ingress 的 timer cycle 仍然產不出任何 accepted atom**。

`2026-07-28T07:00:42Z` 那輪 timer 是修復後第一個有 ingress 的 cycle：`split_sessions=1`、`slices=0`、`status=partial`，session `claude-code:9105547d…` 被 park。parked evidence 的 `attempts_detail` 顯示 claude 跑了 299.7s 後 `timeout`（`stdout_bytes=0`、最後一次呼叫只剩 2 秒），codex 接手時只剩 1 秒。

根因是算術，不是契約：

| 量測項 | 值 |
|---|---|
| claude 單 chunk | 190.5s（另一次量測 244s） |
| cg 單 chunk | 26.0s |
| 47 fragments／212,950 bytes 的 session | 打包成 **7 chunks** |
| chain 預算 `FIXED_SESSION_DEADLINE_SECONDS` | 600s |
| 單次呼叫上限 `FIXED_TIMEOUT_SECONDS` | 300s |

7 × 190s ≈ 1,330s，是 chain 預算的兩倍以上。router 給每次呼叫的是 `min(profile.timeout, remaining_seconds)`，所以 tier-1 跑到第二、三個 chunk 就把整條鏈的時間用完，後續 profile 只拿得到個位數秒。

第二層問題使浪費加倍：`ExternalAgentRouter.run_session()` 的契約是「每個 chunk 都驗證通過才算該 profile 成功」，**後段 chunk 失敗會丟棄前段所有已驗證的 outputs**，下一個 profile 從 chunk 0 重跑。claude 那 299.7s 內確實已產出合法 JSON（同一批 fragments 的 chunk 0 單獨重跑得 exit 0／190.5s／15,858 bytes／`parse_response()` 通過），卻整批作廢。

預期結果：大 session 不再必然 park；有 ingress 的 timer cycle 能穩定產出至少一個 accepted atom，使 AR-11 soak 具備累積條件；已經產出的合法內容不因後段失敗而丟棄。

## Goals

- 整條 fallback 鏈的時間預算隨該 session 實際的 chunk 數縮放，而非固定 600s；單次呼叫 300s 的 hang 防護維持不變。
- 明顯超出某個 profile 可完成範圍的 session，不再耗盡預算才失敗，而是在耗用 agent call 之前就判定該 profile 不適用並讓路給更快的 profile。
- 後段 chunk 失敗時，前段已驗證的 chunk 成果可被保留與續用，且 provenance 誠實記載哪個 chunk 由哪個 profile 產出。
- 既有的失敗語意不被弱化：真正耗盡時仍 park、仍寫 parked evidence，`attempts_detail` 仍可區分 `timeout`／`invalid_output`／`empty_output`。

## Non-Goals

- 不調整 `FIXED_TIMEOUT_SECONDS`（單次呼叫 300s）。放寬單次上限等於放棄 hang 防護。
- 不處理 #74（`local-vllm` map-reduce 只切輸出不切輸入）。那是另一支 harness 的獨立缺陷。
- 不改變 chunk 打包演算法本身（`budget.pack_prompt_chunks` 的 token/bytes 上限維持不動）。降低 chunk 數需要重新評估截斷與品質風險，不在本次範圍。
- 不改變 atomize 的輸出 schema 或 slice frontmatter 的既有欄位語意。
