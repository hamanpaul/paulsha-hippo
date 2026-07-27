---
status: accepted
work_item: issue-64-ledger-torn-line-repair
---

## Context

`runtime/ledger/import.jsonl` line 230 含 577 個 `\x00`，插在一筆 `copilot-cli:799ae7d0-1925-4545-b32b-0e3e146d74b5` 記錄中間。全行 1272 bytes，移除 NUL 後剩 695 bytes 的完整 JSON，`recorded_at` 與前後鄰居時間戳吻合。

判定鏈：`janitor/scanner.py:171` 對解析失敗行附加 warning `skipped N bad line(s)` → `dream/orchestrator.py:_run_pass` 見 warnings 非空即回傳 `False` → `dream/orchestrator.py:130` 判 `status = "partial"`。

約束：ledger 是 append-only 契約；`import.jsonl` 已 24,139 行 / 16MB；health 掃描每輪 dream 都跑，成本敏感。

完整背景見 `docs/superpowers/specs/2026-07-27-issue-64-ledger-torn-line-repair-design.md`。

## Goals / Non-Goals

Goals：

- 讓撕裂行可被偵測、可被指名（檔名 + 行號），不再只能從 opaque 的 `partial` 反推。
- 可救回的行救回內容；不可救回的行讓 janitor 停止無限重複 warn。
- 修復操作原子、冪等、可回滾。
- 修正 `invalid_frontmatter` 因 MOC 索引檔而恆不歸零的假陽性。

Non-Goals：

- 不變更 `dream/orchestrator.py` 的 warning → partial 判定語意。
- 不處理 `invalid_checksum: 18`（尾端換行造成的 checksum 漂移）。
- 不處理 `runtime/queue` 缺 sweeper。

## Decisions

### 決策一：可救回的行原地救回，不可救回的雙邊隔離

移除 NUL 後可解析的行原地救回。理由：該 695 bytes 的 JSON 內容完好，NUL 是檔案系統層的垃圾、從來不是任何 append 真正寫入的內容，因此屬於復原歷史而非改寫歷史。

替代方案：

- 純附加隔離（完全不碰原檔）：不牴觸 append-only 契約，但壞行永遠留存，未來新寫的 reader 仍會踩到，且該筆記錄的內容等同放棄。
- 只偵測不修：PR 最小，但每次復發都需人工處置，與「瑕疵潛伏 60 天無人發現」的教訓相悖。

### 決策二：隔離必須雙邊生效

quarantine 若只是寫一份清單而 janitor 不讀，不可救回行仍會讓 dream 永久 partial——問題原封不動。因此寫入端（`<name>.jsonl.quarantine`）與讀取端（janitor bad-line 計數排除已隔離行）必須成對交付。

以內容 sha256 為主鍵、行號為輔助鍵：append-only 只在尾端加行，既有行號穩定；repair 不刪行，行數也不變。

### 決策三：用 frontmatter 欄位而非檔名辨識 MOC

`moc/moc_builder.py:_write_moc` 寫入 `memory_layer: moc`，knowledge slice 為 `memory_layer: knowledge`。用欄位判別比比對 `*-moc.md` 檔名穩固，未來改命名不會失效。

替代方案：比對檔名後綴——實作更短，但把命名慣例變成隱性契約。

### 決策四：沿用既有維護命令形狀

`hippo ledger repair` 照 `locks cleanup-legacy` 的既有慣例：`--memory-root` 必填、預設 dry-run、`--apply` 才寫入。維持同類破壞性維護操作的一致心智模型。

## Risks / Trade-offs

- **改寫 append-only 檔案的先例** → 只移除 NUL、其餘位元組逐一不變，並以測試逐位元組驗證；`--apply` 前強制備份；dry-run 為預設。
- **備份寫入成功但替換失敗，留下孤兒備份** → 備份採含時間戳的固定命名，doctor 可指認；替換失敗時原檔維持原狀，不進入半修復狀態。
- **quarantine 清單本身損毀導致壞行被誤放行** → fail-closed：清單不可讀時該檔的 bad line 一律照計。
- **行號作為輔助鍵在未來若引入 ledger compaction 會失效** → 主鍵為內容 sha256，行號僅為輔助；compaction 若實作需一併重寫清單，於 spec 標明。
- **health 掃描排除 MOC 後，真正損壞的 MOC 檔不再被計入** → MOC 為每輪 dream 重新生成的衍生物，其正確性由 moc pass 自身保證，不屬 slice health 範疇。

## Migration Plan

1. 合併後備份 `import.jsonl`。
2. `hippo ledger repair --memory-root ~/.agents/memory`（dry-run）確認只有 line 230 被列為可救回。
3. `--apply` 執行修復。
4. `hippo requeue` 兩個卡住的 parked session（`claude-code__3c0a8d08…` 68 frags、`claude-code__66f5eed9…` 51 frags）。
5. `hippo dream run` 確認 `status: "ok"` 且 `invalid_frontmatter: 0`。

回滾：還原 `.bak-repair-<ts>` 備份即可，quarantine 清單為附加檔可直接刪除。

## Open Questions

無。設計決策已於 2026-07-27 與維護者確認定案。
