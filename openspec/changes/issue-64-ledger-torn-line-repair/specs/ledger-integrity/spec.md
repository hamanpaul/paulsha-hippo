## ADDED Requirements

### Requirement: Ledger 撕裂行偵測與分類

診斷輸出 SHALL 唯讀掃描 `runtime/ledger/` 下的每個 `*.jsonl`，將每一行分類為正常、可救回（移除所有 `\x00` 後可解析為 JSON）或不可救回（移除 `\x00` 後仍不可解析），並 SHALL 指名每個受影響檔案與其壞行行號。診斷 MUST NOT 修改任何 ledger 檔案。

#### Scenario: 撕裂行被指名
- **WHEN** 某 ledger 檔案含一行嵌有 NUL 位元組、但移除後可解析為 JSON 的記錄
- **THEN** 診斷 SHALL 將該行歸類為可救回，並輸出檔名與行號

#### Scenario: 不可救回行被區分
- **WHEN** 某 ledger 檔案含一行移除 NUL 後仍不可解析的記錄
- **THEN** 診斷 SHALL 將該行歸類為不可救回，與可救回行分別計數

#### Scenario: 診斷不觸碰檔案
- **WHEN** 對含壞行的 ledger 執行診斷
- **THEN** 所有 ledger 檔案的位元組內容 SHALL 維持不變

### Requirement: 可救回行的原子救回

修復命令 SHALL 預設為 dry-run，僅在明確指定套用旗標時寫入。套用時 SHALL 先建立含時間戳的備份，逐行重建內容時僅自可救回行移除 `\x00`、其餘所有位元組逐一保持不變，並 SHALL 以同目錄暫存檔加 fsync 後原子替換目標檔。備份建立失敗或替換失敗時 SHALL 中止且原檔維持原狀。

#### Scenario: 預設為 dry-run
- **WHEN** 未指定套用旗標執行修復命令
- **THEN** 命令 SHALL 報告將被修復的行，且所有 ledger 檔案的 SHA-256 SHALL 維持不變

#### Scenario: 救回只移除 NUL
- **WHEN** 對含可救回行的 ledger 套用修復
- **THEN** 該行 SHALL 可解析為 JSON，且檔案其餘所有位元組 SHALL 與修復前逐一相同

#### Scenario: 修復前先備份
- **WHEN** 套用修復並成功寫入
- **THEN** SHALL 存在含時間戳的備份檔，其內容 SHALL 與修復前的原檔逐位元組相同

#### Scenario: 冪等
- **WHEN** 對已修復完成的 ledger 再次套用修復
- **THEN** 命令 SHALL 為 no-op，SHALL NOT 寫入目標檔，且 SHALL NOT 產生新備份

### Requirement: 不可救回行的雙邊隔離

不可救回行 SHALL 原樣保留於原檔中，不得刪除。修復 SHALL 將其記入同目錄的 quarantine 清單，每筆包含行號、內容 SHA-256、原因與隔離時間。消費該 ledger 的掃描端 SHALL 將已隔離的行排除在壞行計數之外，使後續週期不再重複回報同一個 warning。quarantine 清單不可讀時 SHALL fail-closed，該檔壞行一律照計。

#### Scenario: 不可救回行被隔離而非刪除
- **WHEN** 對含不可救回行的 ledger 套用修復
- **THEN** 該行 SHALL 仍存在於原檔且內容不變，並 SHALL 於 quarantine 清單中留下行號、SHA-256、原因與隔離時間

#### Scenario: 已隔離行不再觸發 warning
- **WHEN** 掃描端處理一個所有壞行皆已隔離的 ledger
- **THEN** 壞行計數 SHALL 為零，且 SHALL NOT 產生壞行 warning

#### Scenario: quarantine 清單損毀時 fail-closed
- **WHEN** quarantine 清單存在但無法解析
- **THEN** 掃描端 SHALL 將該 ledger 的壞行全數計入，SHALL NOT 因清單不可讀而放行
