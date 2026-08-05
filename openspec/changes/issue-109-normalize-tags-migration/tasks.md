# Tasks: issue-109-normalize-tags-migration

- [x] Task 1: scan + dry-run <!-- id: 0 -->
  - [x] 先寫測試：建 tmp memory root 放 3 個 fixture slice（正常 tags／含裸 int tag／含 None 與嵌套 list 的 tags），`--dry-run` 回報恰好 2 個待修 slice 與各自的正規化後 tags 預覽，不改任何檔案（bytes 比對）。 <!-- id: 1 -->
  - [x] 實作 scan：走訪 knowledge 層 markdown，YAML frontmatter 的 `tags` 含非字串元素者列入。 <!-- id: 2 -->
- [x] Task 2: apply + 冪等 <!-- id: 3 -->
  - [x] 先寫測試：`--apply` 後該 2 個 slice 的 tags 全為字串、body 逐位元不變、其他 frontmatter 欄位 parsed 值不變（parse-equivalent，見 design D4：update() 整份重 dump，表層引號樣式可正規化；datetime→ISO8601 字串、null 維持 null 為宣告的正規化）；含 production-shaped fixture 全欄位斷言；再跑 `--dry-run` 回報 0；再跑一次 `--apply` 為 no-op。 <!-- id: 4 -->
  - [x] 先寫測試（回歸 #104 保護）：apply 後對其中一個 slice 呼叫 `frontmatter_io.update()`（模擬 retitle），重新解析 tags 仍全為字串。 <!-- id: 5 -->
  - [x] 實作 apply：`normalize_tags()` + `frontmatter_io.update()`。 <!-- id: 6 -->
  - [x] CLI 接線 `hippo knowledge normalize-tags`；全套綠；`changelog.d/109-normalize-tags-migration.md`（type: fix）。 <!-- id: 7 -->

