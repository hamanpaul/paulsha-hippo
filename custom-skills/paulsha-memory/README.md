# `custom-skills/paulsha-memory`

此目錄是 `paulsha-memory` 的 repo 內 staging scaffold，用來對齊未來 sync-back 到 `hamanpaul/custom-skills` 的最小結構。

## sync-back gate

回寫 `custom-skills/paulsha-memory` 前，必須同時滿足：

1. 完成 Stage 2 importer / classifier / replay stage 測試。
2. 保留 `decayed/reactivation` 規則與測試證據。
3. 證據落地於 `docs/superpowers/workstreams/stage2-paulsha-memory/evidence/`。
4. `review.md` 無阻斷性結論，且 archive 已更新。
5. 不在 Stage 2 自行擴充 Stage 3 frontmatter schema（必填欄位 `slice_id / artifact_kind / supersedes / checksum` 由 Stage 3 擁有）。

## 邊界

- 這裡只存放 sync-back 前需要對齊的結構與規範。
- 真正 runtime 載入仍以外部 skill plugin 安裝管道為準。
