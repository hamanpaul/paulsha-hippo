### Fixed
- ledger 撕裂行（嵌入 NUL 位元組的 JSONL 行）不再讓 janitor 每輪回報壞行、使
  `hippo dream run` 永久停在 `partial`。新增 `hippo doctor` 的 ledger 完整性檢查與
  `hippo ledger repair`（預設 dry-run，`--apply` 才寫入）：可救回的行原地救回（先備份、
  同目錄 temp + fsync + 原子替換、只移除 NUL、其餘位元組逐一不變、冪等），不可救回的行
  原樣保留並記入 `runtime/ledger/<name>.jsonl.quarantine`，janitor 的壞行計數排除已隔離行
  （清單不可解析時 fail-closed）。
- dream health 的 `invalid_frontmatter` 不再把生成的 MOC 索引檔當成缺欄位的 slice。改以
  frontmatter `memory_layer: moc` 判別而非檔名慣例；該指標先前恆等於 MOC 檔數而無法歸零。
