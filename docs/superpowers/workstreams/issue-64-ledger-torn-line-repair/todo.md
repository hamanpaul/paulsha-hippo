---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

# issue-64-ledger-torn-line-repair / todo

規劃三件套：

- spec — `docs/superpowers/specs/2026-07-27-issue-64-ledger-torn-line-repair-spec.md`
- design — `docs/superpowers/specs/2026-07-27-issue-64-ledger-torn-line-repair-design.md`
- plan — `docs/superpowers/plans/2026-07-27-issue-64-ledger-torn-line-repair.md`

## Tasks

- [ ] ledger 行分類核心（`classify_line` / `line_sha256`，空白行歸不可救回）
- [ ] 檔案與目錄唯讀掃描（`scan_file` / `scan_ledger_dir`，回報行號）
- [ ] quarantine 清單讀寫，清單損毀時 fail-closed
- [ ] 原子修復（備份 → 逐行重建 → temp + fsync + `os.replace`），冪等
- [ ] janitor 讀取端排除已隔離行（`read_import_records_tolerant`）
- [ ] health 掃描排除帶 `memory_layer: moc` 的生成索引檔
- [ ] `hippo ledger repair` 子命令與 `hippo doctor` ledger 完整性輸出
- [ ] changelog.d 碎片、全套 pytest、policy_check、openspec strict validate

## Blockers

- [ ] 無

## 合併後 runtime 修復（不在 PR diff 內）

- [ ] 修復 `import.jsonl` line 230，requeue 兩個 parked session，確認 dream `status: "ok"`
