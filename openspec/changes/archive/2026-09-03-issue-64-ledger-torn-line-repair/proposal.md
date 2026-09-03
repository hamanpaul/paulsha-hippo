---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

## Why

`runtime/ledger/import.jsonl` line 230 是一筆 2026-06-24 的撕裂寫入（577 個 `\x00` 插在一筆完好的 JSON 記錄中間），使 janitor 每輪回報 `skipped 1 bad line(s)`，經 `_run_pass` 的「warnings 非空即不 clean」判定，讓 `hippo dream run` 永久停在 `status: "partial"`、從未全綠。ledger 是 append-only，這行不會自己消失，而目前沒有任何檢查會指出它——這個瑕疵潛伏了 60 天，只能從一句 opaque 的 `partial` 反推。

既有需求「Complete backlog and health semantics」已規定 malformed **inbox artifact** 必須進入 durable quarantine，避免後續週期無限重複同一個 warning；但該原則未涵蓋 **ledger 行**，這正是本次踩到的缺口。

## What Changes

- 新增 `hippo doctor` 的 ledger 完整性檢查：唯讀掃描 `runtime/ledger/*.jsonl`，將每行分類為正常／可救回（移除 NUL 後可解析）／不可救回，並回報檔名與行號。
- 新增 `hippo ledger repair` 子命令：`--memory-root` 必填、預設 dry-run、`--apply` 才寫入。可救回行原地救回（備份 → 同目錄 temp + fsync + `os.replace`），不可救回行原樣保留不刪除。
- 新增 quarantine 清單 `runtime/ledger/<name>.jsonl.quarantine`，並讓 janitor 的 bad-line 計數排除已隔離的行——隔離必須雙邊生效，否則不可救回行仍會讓 dream 永久 partial。
- 修正 health 指標假陽性：health 掃描依 frontmatter `memory_layer: moc` 排除生成的 MOC 索引檔，不再把它們當成缺欄位的 slice 計入 `invalid_frontmatter`（目前恆為 16，等於 MOC 檔數）。

## Capabilities

### New Capabilities
- `ledger-integrity`: append-only ledger 的位元組層完好性——撕裂行的偵測分類、可救回行的原地救回、不可救回行的雙邊隔離，以及這些操作的原子性與冪等性保證。

### Modified Capabilities
- `atomization-release-integrity`: 「Complete backlog and health semantics」的 health 計數需排除生成的 MOC 索引檔。MOC 檔依設計不帶 slice frontmatter，將其計入 `invalid_frontmatter` 使該指標永遠無法歸零、失去指示價值。

## Impact

- `paulsha_hippo/cli.py`：新增 `ledger repair` 子命令與 `doctor` 的 ledger 檢查輸出。
- `paulsha_hippo/janitor/scanner.py`：bad-line 計數排除已隔離行。
- `paulsha_hippo/ledger/dream.py`：health 掃描排除 `memory_layer: moc`。
- 新增檔案契約 `runtime/ledger/<name>.jsonl.quarantine`（JSONL）。
- 不變更 `dream/orchestrator.py` 的 warning → partial 判定語意；dream status 是對外契約，#20 / #34 均引用該語意，修掉壞資料後 warning 自然消失即可轉綠。
