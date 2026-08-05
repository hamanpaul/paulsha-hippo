---
type: fix
---
- 提供 `hippo knowledge normalize-tags --memory-root <root> [--dry-run|--apply]` 指令與 `paulsha_hippo/tags_migration.py` 模組，提供一次性回填修復磁碟上既存非字串 tags slice（如裸數字 320）的遷移工具 (#109)。
- 實現冪等掃描與改寫：`--dry-run` 報告待修 slice 數量與正規化預覽且不修改檔案；`--apply` 使用 `normalize_tags()` 正規化 tags 並以 `frontmatter_io.update()` 安全改寫 frontmatter，語意契約為 parse-equivalent——body 逐位元不變、tags 以外欄位 parsed 值不變（表層引號樣式可正規化；未引號 datetime 正規化為等值 ISO8601 字串、null 維持 null）。再次執行 `--dry-run` 回報 0。
- 掃描無條件過濾 `memory_layer != "knowledge"`（比照 rekey/linker 慣例）：`--memory-root` 打錯（無 knowledge/ 子目錄）時不會改寫 inbox/episodic 或一般 markdown 文件；非 list scalar tags 依 #101 語意抹為 `[]`，決策以測試明文鎖住。
- 修正 `moc/frontmatter_io.py::_scalar()`：None 改輸出 YAML `null`（原輸出 `None` 字面值，`update()` 往返後劣化成字串 `"None"`），比照 #102/#104 型別保真精神 (#109 review)。
