---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

# Ledger 撕裂行修復與 health 指標假陽性（issue #64）規格

## Problem and Outcome

`runtime/ledger/import.jsonl` line 230 是一筆 2026-06-24 的撕裂寫入——577 個 `\x00` 插在一筆完好的 JSON 記錄中間。janitor 每輪因此回報 `skipped 1 bad line(s)`，而 `_run_pass` 只要 warnings 非空即判 pass 不 clean，使 `hippo dream run` 永久停在 `status: "partial"`。ledger 為 append-only，該行不會自行消失，且目前沒有任何檢查會指名它——這個瑕疵潛伏 60 天，只能從一句 opaque 的 `partial` 反推。

同時 health 的 `invalid_frontmatter` 恆為 16，等於生成的 MOC 索引檔數；該指標永遠無法歸零，失去診斷價值。

預期結果：撕裂行可被指名、可救回、可隔離；修復後 dream 轉為 `ok`；`invalid_frontmatter` 能反映真實的 slice 損壞數。

## Goals

- 診斷可指名每個撕裂行的檔名、行號與可救回性。
- 可救回的行救回其內容；不可救回的行讓消費端停止無限重複 warn。
- 修復操作原子、冪等、可回滾。
- health 計數只涵蓋 knowledge slice，不含生成的衍生產物。

## Requirements

### 撕裂行偵測與分類

診斷 SHALL 唯讀掃描 `runtime/ledger/*.jsonl`，將每行分類為正常、可救回（移除 `\x00` 後可解析）或不可救回，並 SHALL 指名檔名與行號。診斷 MUST NOT 修改任何 ledger 檔案。

空白行歸類為不可救回：既有 reader 也把空白行計為 bad line，若視為正常則「修復後壞行計數歸零」的不變量不成立。

### 可救回行的原子救回

修復 SHALL 預設 dry-run，僅在明確指定套用旗標時寫入。套用時 SHALL 先建立含時間戳的備份，僅自可救回行移除 `\x00`、其餘所有位元組逐一保持不變，並 SHALL 以同目錄暫存檔加 fsync 後原子替換。備份或替換失敗時 SHALL 中止且原檔維持原狀。無可救回行時 SHALL 為 no-op、不寫入、不備份。

### 不可救回行的雙邊隔離

不可救回行 SHALL 原樣保留於原檔中，不得刪除，並 SHALL 記入 quarantine 清單（行號、內容 SHA-256、原因、隔離時間）。消費端 SHALL 將已隔離行排除在壞行計數之外。quarantine 清單不可解析時 SHALL fail-closed，該檔壞行一律照計。

隔離必須雙邊生效：只寫清單而消費端不讀，dream 仍會永久 partial，問題原封不動。

### health 計數排除生成的 MOC 索引檔

health 的 `invalid_frontmatter`、`generic_title`、`unknown_project` SHALL 排除帶 `memory_layer: moc` 的生成索引檔。以 frontmatter 欄位而非檔名慣例判別。

## Non-Goals

- 不變更 `dream/orchestrator.py` 的 warning → partial 判定語意。dream status 是對外契約，#20 / #34 均引用該語意；修掉壞資料後 warning 自然消失即可轉綠。
- 不處理 `invalid_checksum: 18`（尾端換行造成的 checksum 漂移，2026-07-24 備份中即已存在）。
- 不處理 `runtime/queue` 缺 sweeper（2 筆 payload 滯留 18 天）。

## Acceptance

- `hippo doctor` 對現行 memory root 指出 `import.jsonl` line 230 為可救回。
- `hippo ledger repair --apply` 後該行可解析，檔案其餘位元組逐一不變，備份存在。
- 修復後 `hippo dream run` 回報 `status: "ok"`、`health.invalid_frontmatter` 為 `0`。
- 全套 pytest 通過；`policy_check` 無 failure；`openspec validate issue-64-ledger-torn-line-repair --strict` 通過。
