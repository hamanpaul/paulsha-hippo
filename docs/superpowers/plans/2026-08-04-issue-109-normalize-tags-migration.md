---
status: accepted
work_item: issue-109-normalize-tags-migration
---

# 既存非字串 tags 一次性 migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development——先寫失敗測試再實作。

**Goal:** 提供 `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]`，一次性回填修復磁碟上既存的非字串 tags slice（如裸數字 `320`），使 MOC index 不再每輪因 `invalid tags type` warning 把 dream 判成 partial。

**範圍註記：** issue #109 第 2 段（`_scalar()` 純數字字串引號）已由 PR #104 修復並含 round-trip 測試，本 plan 只做第 1 段（資料 migration）。known 案例：`knowledge/github.com-hamanpaul-intellidbgkit--p-7ae7dbfe5516/terminal-directory-seal-and-crash-recovery--sl-1010b51101ebe096.md` 第 23 行裸數字 `320`。

**Architecture:** 仿 repo 既有三個 dry-run/apply 一次性遷移模板：`paulsha_hippo/retitle.py`、`paulsha_hippo/rekey.py`、`paulsha_hippo/moc/entity_hub.py`。重用 `paulsha_hippo/atomizer/slice_frontmatter.py::normalize_tags()` 做正規化、`paulsha_hippo/moc/frontmatter_io.py::update()` 做改寫（#104 已保證純數字字串往返不劣化）。

**Target branch:** `feature/109-normalize-tags-migration`

## Global Constraints

- 語言 zh-tw；TDD 先 RED 再實作。
- 交付治理：`changelog.d/<slug>.md` 碎片、pytest／policy_check／openspec strict 全綠、PR template checklist 全勾、body `Closes #109`。
- 全套測試在非巢狀 sibling worktree 跑（巢狀下 test_project_resolver 2 個假失敗為已知）。
- 純附加：不動 MOC index 的嚴格驗證（它是正確的最後防線）、不動熱路徑。
- migration 必須冪等：apply 後再跑 dry-run 零殘留。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。

## Task 1: scan + dry-run

**檔案**：新模組（建議 `paulsha_hippo/tags_migration.py`）、`paulsha_hippo/cli.py`、`tests/`（新測試檔）

- [ ] 先寫測試：建 tmp memory root 放 3 個 fixture slice（正常 tags／含裸 int tag／含 None 與嵌套 list 的 tags），`--dry-run` 回報恰好 2 個待修 slice 與各自的正規化後 tags 預覽，不改任何檔案（bytes 比對）。
- [ ] 實作 scan：走訪 knowledge 層 markdown，YAML frontmatter 的 `tags` 含非字串元素者列入。

## Task 2: apply + 冪等

- [ ] 先寫測試：`--apply` 後該 2 個 slice 的 tags 全為字串、body 逐位元不變、其他 frontmatter 欄位 parsed 值不變（parse-equivalent：update() 整份重 dump，表層引號樣式可正規化；datetime→ISO8601 字串、null 維持 null 為宣告的正規化，見 openspec design D4）；含 production-shaped fixture 全欄位斷言；再跑 `--dry-run` 回報 0；再跑一次 `--apply` 為 no-op。
- [ ] 先寫測試（回歸 #104 保護）：apply 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle），重新解析 tags 仍全為字串。
- [ ] 實作 apply：`normalize_tags()` + `frontmatter_io.update()`。
- [ ] CLI 接線 `hippo knowledge normalize-tags`；全套綠；`changelog.d/109-normalize-tags-migration.md`（type: fix）。
