---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

# Issue #64 ledger 撕裂行修復 tasks

依 TDD：每個實作任務前先落紅燈測試。測試置於 `tests/test_ledger_integrity.py`（新增）與既有 janitor / dream health 測試檔。

## 1. 撕裂行分類（偵測核心）

- [ ] 1.1 紅燈：三分類測試——正常行、可救回行（嵌入 `\x00`、移除後可解析）、不可救回行（移除 `\x00` 後仍不可解析），驗證分類結果與行號正確
- [ ] 1.2 實作分類函式：唯讀掃描單一 `*.jsonl`，回傳每行分類與行號
- [ ] 1.3 紅燈：掃描不得修改檔案——掃描前後所有 ledger 檔的 SHA-256 不變
- [ ] 1.4 實作 `runtime/ledger/` 全目錄掃描聚合

## 2. `hippo doctor` ledger 完整性輸出

- [ ] 2.1 紅燈：doctor 對含壞行的 memory root 輸出受影響檔名、行號與可救／不可救分類計數
- [ ] 2.2 接上 doctor 輸出；確認 ledger 目錄不存在或無 `*.jsonl` 時回報 0 筆且不報錯
- [ ] 2.3 量測 doctor 在真實規模（`import.jsonl` 24k 行 / 16MB、共 32 個 ledger 檔）的耗時；若顯著拖慢互動式 doctor，改為先以位元組層快篩再逐行解析

## 3. `hippo ledger repair` 命令

- [ ] 3.1 紅燈：預設 dry-run——未帶 `--apply` 時報告待修復行，且所有 ledger 檔 SHA-256 不變
- [ ] 3.2 紅燈：救回只移除 NUL——`--apply` 後該行可解析，且檔案其餘位元組與修復前逐一相同
- [ ] 3.3 紅燈：備份存在且與修復前原檔逐位元組相同
- [ ] 3.4 紅燈：冪等——對已修復檔再次 `--apply` 為 no-op、不寫入、不產生新備份
- [ ] 3.5 實作命令：`--memory-root` 必填、預設 dry-run、`--apply` 才寫入（沿用 `locks cleanup-legacy` 形狀）
- [ ] 3.6 實作寫入路徑：備份 → 逐行重建 → 同目錄 temp + fsync + `os.replace`
- [ ] 3.7 紅燈 + 實作：備份失敗或替換失敗時中止，原檔維持原狀，不進入半修復狀態

## 4. Quarantine 雙邊隔離

- [ ] 4.1 紅燈：不可救回行 `--apply` 後仍原樣存在於原檔，且 quarantine 清單留下行號、SHA-256、原因、隔離時間
- [ ] 4.2 實作 quarantine 寫入端 `runtime/ledger/<name>.jsonl.quarantine`（JSONL）
- [ ] 4.3 紅燈：janitor 對「所有壞行皆已隔離」的 ledger 壞行計數為 0 且不產生 warning
- [ ] 4.4 實作 janitor 讀取端：`_build_import_index` 的壞行計數排除已隔離行，以內容 SHA-256 為主鍵、行號為輔助
- [ ] 4.5 紅燈 + 實作：quarantine 清單存在但不可解析時 fail-closed，該檔壞行全數照計

## 5. health 指標假陽性

- [ ] 5.1 紅燈：knowledge 樹含生成的 MOC 索引檔（`memory_layer: moc`）時，`invalid_frontmatter` 為 0
- [ ] 5.2 紅燈：真正缺必要欄位且非 MOC 的 slice 仍計入 `invalid_frontmatter`
- [ ] 5.3 實作：`ledger/dream.py` health 掃描依 `memory_layer == "moc"` skip，同時排除於 `generic_title` 與 `unknown_project` 計數

## 6. 交付治理

- [ ] 6.1 新增 `changelog.d/64-ledger-torn-line-repair.md` 碎片（zh-tw）
- [ ] 6.2 `VERSION` 維持 `0.1.1` 不動（非 release PR）
- [ ] 6.3 全套 `pytest` 通過
- [ ] 6.4 `python3 -m policy_check --repo .` 無 failure
- [ ] 6.5 `openspec validate issue-64-ledger-torn-line-repair --strict` 通過
- [ ] 6.6 PR body 帶 `Closes #64`，checklist 全勾

## 7. 合併後 runtime 修復（不在 PR diff 內）

- [ ] 7.1 備份 `~/.agents/memory/runtime/ledger/import.jsonl`
- [ ] 7.2 dry-run 確認僅 line 230 列為可救回
- [ ] 7.3 `--apply` 修復 line 230
- [ ] 7.4 `hippo requeue` 兩個 parked session（`claude-code__3c0a8d08…` 68 frags、`claude-code__66f5eed9…` 51 frags）
- [ ] 7.5 `hippo dream run` 確認 `status: "ok"` 且 `invalid_frontmatter: 0`
