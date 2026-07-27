---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

# Ledger 撕裂行修復與 health 指標假陽性（issue #64）設計

- 日期：2026-07-27
- Issue：[#64](https://github.com/hamanpaul/paulsha-hippo/issues/64)
- 狀態：已核可，待實作

## 背景

`hippo dream run` 長期回報 `status: "partial"`，從未全綠。實測定位到單一根因與一項指標失真。

### 根因一：`import.jsonl` 撕裂寫入令 dream 永久 partial

`runtime/ledger/import.jsonl` line 230 是一筆 2026-06-24 的撕裂寫入——577 個 `\x00` 插在一筆 `copilot-cli:799ae7d0-1925-4545-b32b-0e3e146d74b5` 記錄中間。該行共 1272 bytes，移除 NUL 後剩 695 bytes 的完整 JSON，`recorded_at` 與前後鄰居時間戳吻合。

判定鏈：

1. `janitor/scanner.py:171` — `import.jsonl` 解析失敗行數 > 0 時附加 warning `skipped N bad line(s)`。
2. `dream/orchestrator.py:_run_pass` — pass 回傳的 `warnings` 為非空 list 即回傳 `False`。
3. `dream/orchestrator.py:130` — `status = "ok" if (atomize_clean and janitor_clean and moc_clean) else "partial"`。

`import.jsonl` 是 append-only，這行不會自己消失，因此一行 60 天前的壞資料讓 dream 永遠不可能全綠。

### 根因二：`invalid_frontmatter: 16` 是假陽性

`ledger/dream.py:176` 用 `knowledge.rglob("*.md")` 掃全樹計算 health，把生成的 MOC 索引檔也當成 slice 檢查。這些檔案依設計沒有 `slice_id` / `checksum`。數字恆等於 MOC 檔數（16）。

## Decisions

### 決策一：可救回的行原地救回，不可救回的隔離（A + B fallback）

移除 NUL 後可解析的行**原地救回**：那 695 bytes 的 JSON 內容完好，NUL 是檔案系統層的垃圾、從來不是任何 append 真正寫入的內容，因此這是復原歷史而非改寫歷史。不可救回的行則保留原樣、改以 quarantine 清單隔離，原檔位元組不動。

被否決的替代方案：

- **純附加隔離（只做 B）**：完全不碰 append-only 契約，但壞行永遠留在檔裡，未來新寫的 reader 仍會踩到，且該筆記錄的內容等同放棄。
- **只偵測不修（只做 C）**：PR 最小，但每次復發都得人工處理，與「這個瑕疵潛伏 60 天沒人發現」的教訓相悖。

### 決策二：用 frontmatter 欄位而非檔名辨識 MOC

`moc/moc_builder.py:_write_moc` 寫入 `memory_layer: moc`，knowledge slice 則是 `memory_layer: knowledge`。用欄位判別比比對 `*-moc.md` 檔名穩固，未來改命名也不會失效。

## 元件

### 1. 偵測：`hippo doctor` 的 ledger 完整性檢查

唯讀掃描 `runtime/ledger/*.jsonl`，每行分三類：

| 分類 | 判準 |
|------|------|
| ok | 直接 `json.loads` 成功 |
| recoverable | 移除 `\x00` 後 `json.loads` 成功 |
| unrecoverable | 移除 `\x00` 後仍失敗 |

輸出每檔的壞行數與行號。目的是讓這類瑕疵不再只能從一句 opaque 的 `partial` 反推。

### 2. 修復：`hippo ledger repair`

沿用 `locks cleanup-legacy` 既有慣例：`--memory-root` 必填、預設 dry-run、`--apply` 才寫入。

`--apply` 執行順序：

1. 備份 `<name>.jsonl.bak-repair-<ts>`。
2. 逐行重建：可救回行移除 NUL；**不可救回行原樣保留在檔中，不刪除**。
3. 同目錄 temp file + fsync + `os.replace` 原子替換。
4. 無任何可救回行時不寫入、不備份（冪等）。

### 3. Quarantine 清單

不可救回行若只是留在檔裡，janitor 會繼續 warn，dream 仍然 partial。因此隔離必須是雙邊的：

- 寫入端：`runtime/ledger/<name>.jsonl.quarantine`（JSONL，每筆 `{line_no, sha256, reason, quarantined_at}`）。
- 讀取端：`janitor/scanner.py` 的 bad-line 計數排除已隔離的行。

以內容 sha256 為主鍵、行號為輔助。append-only 只在尾端加行，既有行號穩定；repair 不刪行，行數也不變，因此行號可安全作為輔助鍵。

### 4. health 掃描排除 MOC

`ledger/dream.py` 讀完 frontmatter 後，`memory_layer == "moc"` 即 skip，不計入 `invalid_frontmatter`、`generic_title`、`unknown_project`。

## 錯誤處理

- ledger 目錄不存在或無 `*.jsonl`：視為無事可做，回報 0 筆，不報錯。
- 備份寫入失敗：中止且不觸碰原檔。
- temp 寫入或 `os.replace` 失敗：原檔維持原狀，備份保留供人工處置。
- quarantine 檔本身損毀：fail-closed，該檔的 bad line 一律照計，不因清單不可讀而誤放行。

## 測試

- 撕裂行被判為 recoverable；`--apply` 後可解析、**其餘位元組逐一不變**、備份存在。
- 不可救回行進 quarantine、原檔該行不變、janitor 不再計為 bad line。
- 冪等：repair 第二次執行為 no-op，不再產生備份。
- dry-run 前後全檔 sha256 不變。
- health 掃描：MOC 檔不計入 `invalid_frontmatter`；真正損壞的 slice 仍計入。

## 不在範圍

- `dream/orchestrator.py` 的 warning → partial 語意不變更。dream status 是對外契約，#20 / #34 都引用過該語意。修掉壞資料後 janitor warning 自然消失，dream 即可轉綠，不需要改判定。
- `invalid_checksum: 18`（尾端換行造成的 checksum 漂移，2026-07-24 備份中即已存在）另案處理。
- `runtime/queue` fire-and-forget 無 sweeper（2 筆 payload 滯留 18 天）另案處理。

## 合併後的 runtime 待辦

1. 備份 `import.jsonl`。
2. `hippo ledger repair --memory-root ~/.agents/memory --apply` 修復 line 230。
3. `hippo requeue` 兩個卡住的 parked session：`claude-code__3c0a8d08…`（68 frags）、`claude-code__66f5eed9…`（51 frags），合計 119 個 fragment 滯留 inbox。
4. `hippo dream run` 確認 `status: "ok"`。
