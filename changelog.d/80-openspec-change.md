---
type: change
scope: docs
---
### Changed

- 新增 issue #80 的 OpenSpec change `issue-80-atomize-chunk-budget`（proposal／tasks／`stage2-llm-distillation` spec delta）。本次必須走 spec delta 而非單純實作，是因為方向三（已驗證 chunk 跨 profile 保留）直接牴觸既有 canonical 契約——「Deterministic tiered fallback」原文要求 fallback 時「restart the complete session from frozen input」，即後續 profile 必須從 chunk 0 完整重跑。delta 將該語意改為「從第一個未驗證的 chunk 續跑」，同時明訂全域預算隨 chunk 數縮放（per-call 上限不得因此放寬）、profile 可宣告適用的最大 session 大小（超出者 ineligible 且不耗 agent call），並擴充 provenance 要求為「多 profile 分段完成時須逐 chunk 指出產出者」。耗盡仍 park 一次、仍不做 partial publication 的語意維持不變。
