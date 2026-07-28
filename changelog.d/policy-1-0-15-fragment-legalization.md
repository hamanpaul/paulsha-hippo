---
type: fix
scope: policy
---
修正 `changelog.d/` 中 47 個既有碎片使其符合 collate 合法格式（`python3 -m policy_check.changelog collate` 乾跑已驗證通過）：45 個碎片補上缺漏的 YAML frontmatter（依內文原有 Keep-a-Changelog 小節標題對應 `type`：`### Added`→`feat`、`### Changed`→`change`、`### Fixed`→`fix`；3 個無小節標題者依內容語意人工判定），另外 2 個碎片（`48-project-policy-manifest.md`、`release-0-1-2-readiness-rebind.md`）已有 frontmatter 但 `type: chore` 為非法值，改為語意相符的 `type: change`。這些缺漏／非法值皆早於本次 PR 即存在（各自隨對應歷史 PR 一併加入），本次僅修正格式合法性，不變動碎片描述的實質內容。
