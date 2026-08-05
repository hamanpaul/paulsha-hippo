---
status: accepted
work_item: issue-109-normalize-tags-migration
---

# 既存非字串 tags 一次性 migration（issue #109）設計

- 日期：2026-08-04
- Issue：[#109](https://github.com/hamanpaul/paulsha-hippo/issues/109)
- 狀態：已核可，待實作

## 背景

證據與 root cause 見 spec
（`docs/superpowers/specs/2026-08-04-issue-109-normalize-tags-migration-spec.md`）。摘要：
既存磁碟 slice 的 frontmatter `tags` 含裸數字 `320`，MOC index 每輪重建判
`invalid tags type` 而排除、記 warning，令 orchestrator 整輪永久 `partial`，與 #101 同構
（sticky diagnostic 反模式）。#103 只修了未來新寫入路徑；issue 第 2 段的
`_scalar()` 純數字字串引號劣化風險已由 PR #104（closes #102）修復並含 round-trip 測試。
本設計只處理剩餘範圍：既存壞資料的一次性 migration 該怎麼做。

## Decisions

### D1：仿 retitle/rekey/entity_hub 的 dry-run/apply 模板

repo 內已有三個一次性遷移模板：`paulsha_hippo/retitle.py`、`paulsha_hippo/rekey.py`、
`paulsha_hippo/moc/entity_hub.py`，皆採「掃描 → 產生變更清單（dry-run 預覽）→ 逐檔改寫
（apply 落地）」兩階段結構。新模組 `paulsha_hippo/tags_migration.py` 沿用同一結構，CLI
接線比照三者慣例，掛在 `hippo knowledge normalize-tags --memory-root <root>
[--dry-run|--apply]`。選擇仿既有模板而非另造新框架，理由是三者已在生產驗證過
dry-run/apply 的安全邊界（不誤動未命中檔案、失敗不留半套狀態），沿用可直接繼承這些性質。

### D2：重用 normalize_tags() + frontmatter_io.update()，不新造正規化/序列化邏輯

- 正規化：`paulsha_hippo/atomizer/slice_frontmatter.py::normalize_tags()` 已是 #103
  落地的正規化單一真理來源（新寫入路徑 `build_from_proposal()` 已使用它）。既存資料
  migration 直接重用同一函式，避免出現兩套 tags 正規化規則互相分岔的風險。
- 序列化：`paulsha_hippo/moc/frontmatter_io.py::update()` 已由 PR #104 補上
  `_needs_quoting_for_type_fidelity()`，純數字字串 tag 經 `update()` 往返不再被 YAML
  剝回 int。apply 階段直接呼叫 `update()` 落地改寫，不另寫 frontmatter dump 邏輯，天然
  繼承 #104 的型別保真保護，且與既有三個模板使用同一改寫入口一致。

### D3：冪等性

migration 必須支援重複執行不產生額外變更或副作用：

- scan 邏輯只挑出「`tags` 含至少一個非字串元素」的 slice；`--apply` 後同一 slice 的
  tags 已全為字串，下一輪 scan 自然不會再命中，不需要額外的「已處理」標記機制。
- `--apply` 對「已無非字串 tags」的 slice 是 no-op：不改寫、不觸碰檔案 mtime/checksum，
  避免每次重跑都造成無意義 diff。
- 驗收方式：`--apply` → `--dry-run` 回報 0 待修 slice → 再次 `--apply` 為 no-op（bytes
  與上一次 apply 後逐位元相同）。
- 回歸保護：`--apply` 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle
  等下游呼叫）重新解析 tags 仍全為字串，坐實 #104 的往返修復對本 migration 輸出同樣
  有效，不會出現「migration 修好、下一次 retitle 又劣化回 int」的復發。

## Testing

- `--dry-run` 對 3 個 fixture slice（tags 全字串／含裸 int tag／含 None 與嵌套 list）
  回報恰好 2 個待修 slice 及各自正規化後 tags 預覽，不改任何檔案（bytes 逐位元比對）。
- `--apply` 後該 2 個 slice tags 全為字串，body 與其他 frontmatter 欄位逐位元不變
  （僅 tags 行改動）；再 `--dry-run` 回報 0；再 `--apply` 為 no-op。
- `--apply` 後對其中一個 slice 呼叫 `frontmatter_io.update()`，重新解析 tags 仍全為
  字串（回歸 #104 保護）。
- CLI 接線 `hippo knowledge normalize-tags`；全套 pytest、
  `python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
