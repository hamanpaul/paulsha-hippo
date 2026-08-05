---
status: accepted
work_item: issue-109-normalize-tags-migration
---

# 既存非字串 tags 一次性 migration（issue #109）規格

## Problem and Outcome

2026-08-04 部署 candidate `1299fa1`（含 #103 tag 正規化修復）後，第一輪 dream cycle
（`04:00:08Z`）仍判定 `partial`。根因非新蒸餾失敗（本輪 zero-ingress，backlog 標記無
增長），而是既存磁碟檔案
`knowledge/github.com-hamanpaul-intellidbgkit--p-7ae7dbfe5516/terminal-directory-seal-and-crash-recovery--sl-1010b51101ebe096.md`
（codex 於 2026-08-02T07:52 蒸餾產生，早於本次修復部署）frontmatter `tags` 含裸數字
`320`（第 23 行），MOC index 每輪重建時判 `invalid tags type` 而排除、記 warning，令
orchestrator `moc_clean=False` → 整輪永久 `partial`，直到人工熱修——與 #101 症狀同構
（sticky diagnostic 反模式）。

#103 修復方向正確但範圍侷限於「未來新寫入路徑」（`build_from_proposal()` 呼叫
`normalize_tags()`）；已落地的既存檔案不會被回溯修復，任何在該修復生效前寫入的裸數字
tag 都會持續讓 soak 卡在 partial，直到人工逐檔熱修。

issue #109 原文第 2 段描述的殘留風險——`frontmatter_io.py::_scalar()` 對純數字字串輸出
缺少強制引號，導致任何 `frontmatter_io.update()` 呼叫都可能把已正規化的 tags 於下一次
讀取時劣化回 int——已由 PR #104（closes #102）修復並含 write→read→write→read 往返測試。
本套件不重工該段，只涵蓋 issue 留言「範圍更新」明確標定的剩餘範圍：**既存壞資料的一次
性 migration**。

預期結果：提供一次性、可重跑、有 dry-run 的 migration 工具，掃描並修復既存 knowledge
slice 中非字串 tags，使 MOC index 不再因此類殘留噴 `invalid tags type` warning，dream
cycle 不再被此類 sticky diagnostic 打斷。

## Goals

- G1：提供 `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]` CLI。
- G2：`--dry-run` 列出所有 tags 含非字串元素的既存 slice 及其正規化後 tags 預覽，不修改
  任何檔案（bytes 逐位元不變）。
- G3：`--apply` 重用 `atomizer/slice_frontmatter.py::normalize_tags()` 正規化、
  `moc/frontmatter_io.py::update()` 落地改寫，僅 tags 行變動，body 與其他 frontmatter
  欄位逐位元不變。
- G4：冪等——`--apply` 後重跑 `--dry-run` 回報 0 待修 slice；再次 `--apply` 為 no-op。
- G5：回歸鎖——`--apply` 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle
  等下游呼叫），重新解析 tags 仍全為字串，坐實 PR #104 的往返保護對本 migration 輸出同樣
  有效。

## Non-goals

- 不重工 issue #109 第 2 段（`_scalar()` 純數字字串引號劣化風險）——該風險已由 PR #104
  修復並含 round-trip 測試。
- 不改動 MOC index 的嚴格驗證邏輯（fail-soft warning 是正確的最後防線，維持不動）。
- 不動 dream/atomize/janitor 既有寫入熱路徑，不新增查詢語法或 schema 變動。

## Acceptance

- 已知案例
  `terminal-directory-seal-and-crash-recovery--sl-1010b51101ebe096.md`（tags 含裸數字
  `320`）：`--dry-run` 列出、`--apply` 後修復為字串，MOC rebuild 不再出
  `invalid tags type` warning。
- 全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict`
  全綠。
