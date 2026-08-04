---
type: fix
---
- 提供 `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]` 指令與 `paulsha_hippo/tags_migration.py` 模組，提供一次性回填修復磁碟上既存非字串 tags slice（如裸數字 320）的遷移工具 (#109)。
- 實現冪等掃描與改寫：`--dry-run` 報告待修 slice 數量與正規化預覽且不修改檔案；`--apply` 使用 `normalize_tags()` 正規化 tags 並以 `frontmatter_io.update()` 安全改寫 frontmatter，保留 body 與其他欄位。再次執行 `--dry-run` 回報 0。
