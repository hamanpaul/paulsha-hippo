---
type: change
scope: docs
---
### Changed

- 新增 issue #80（大 session 的 chunk 序列總時間超出 chain 預算、且部分完成成果被整批丟棄）的規劃三件套與 workstream todo，並在 `.cortex/work-items.yaml` 登錄對應 work item `issue-80-atomize-chunk-budget`（連結 issue 與四份 artifact）。三個落地方向依風險遞增排序為獨立 Task：session 預算隨 chunk 數縮放（600s 下限／240s per chunk／1800s 上限，per-call 300s hang 防護不動）、profile 以宣告式 `max_session_chunks` 表達適用範圍（超出者走既有 ineligible 路徑、不耗 agent call、不新增 fallback category）、以及已驗證 chunk 成果跨 profile 保留與續跑（provenance 誠實記載 per-chunk profile，混合時標既有的 `degraded-success`）。本 PR 只含規劃產物，不含實作。
