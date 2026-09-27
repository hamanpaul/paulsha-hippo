# Memory store 佈局與 archive 回收（#151）

## 為什麼要管 store 放哪裡

hippo 的 `memory_root`（預設 `~/.agents/memory`）底下，`archive/`、`runtime/`、`inbox/` 會隨時間累積數以萬計的小檔。若 `memory_root` 經 symlink 解析後落在 **持續同步／被掃描的目錄**（例如 Obsidian vault 由 `ob sync --continuous` 同步）內，同步程序即使已把這些子樹列入雲端忽略清單，本機掃描器仍會逐檔走訪、計算雜湊，造成同步程序記憶體與 page cache 暴增。#151 的實際事故是 WSL2 VM 因此被主機回收（整個 VM 閃退）。

hippo 本身一律以 `memory_root` 存取 store，**沒有任何程式碼寫死 vault 路徑**；問題只出在 store 的物理位置。

## 建議佈局

store 放在同步樹**外**的真實目錄；只有需要在 Obsidian 呈現的 `knowledge/` 以 symlink「借回」vault：

```
~/.agents/memory/                      真實目錄（vault 外）
  ├── knowledge  ─symlink─▶  <vault>/<sub>/memory/knowledge   （留在 vault，續同步）
  ├── archive/  runtime/  inbox/  hooks/  log/  work-centric/  （真實目錄，vault 外）
<vault>/<sub>/memory/
  └── knowledge/                       vault 內只剩這一層
```

- 同步工具只需掃描 knowledge（數千檔），不再掃 archive／runtime。
- hippo 仍在 `~/.agents/memory` 看到完整樹；ledger、provenance 內記錄的絕對路徑前綴不變，**不需要改寫任何紀錄**。
- 不需要在 Obsidian 呈現 knowledge 的主機，直接不做 symlink 即可。

## 偵測與警示

`memory_root`（解析 symlink 後）落在同步／掃描樹內時：

- `hippo doctor` 印出 `storage 位置：⚠ …` 警示行（只報告，不改 exit code）；建議佈局下會顯示 `✓` 並註記 knowledge 借回 vault。
- `hippo dream run` 啟動時、`hippo init` 寫完 config 後，對 stderr 印一行 `[paulsha-hippo] WARN storage：…`（systemd 服務下進 journald）。
- `archive`／`runtime`／`inbox`／`hooks`／`log` 若被個別 symlink 搬進同步樹，也會警示。

偵測方式可設定：

| 設定 | 預設 | 說明 |
|---|---|---|
| `HIPPO_SYNC_MARKERS` | `.obsidian,.stfolder,.dropbox` | 逗號分隔；任一祖先目錄含此名稱的項目即視為同步樹根。設定後**取代**預設清單；值為 `none` 時停用 marker 偵測 |
| `HIPPO_SYNC_ROOTS` | （無） | 以 `:` 分隔的目錄清單，明列為同步／掃描樹（疊加於 marker 偵測），適用沒有 marker 的同步工具 |

## 遷移：把 memory_root 移出 vault（P1）

以下以 `<vault>` 代表 vault 根目錄、`<store>` 代表目前 store 在 vault 內的真實位置（`readlink -f ~/.agents/memory` 的結果）。每一步都可回滾；**請逐步執行並核對輸出**。

### 0. 前置檢查與基線

```bash
VAULT=<vault>
STORE="$(readlink -f ~/.agents/memory)"        # 應落在 $VAULT 底下
case "$STORE" in "$VAULT"/*) echo "store 在 vault 內：需要遷移";; *) echo "store 已不在 vault：不需搬移";; esac

# 同一個 filesystem 時 mv 只是 rename（瞬間完成、不複製資料）；device id 不同則改用複製流程
stat -c '%d %n' "$STORE" ~/.agents

# 基線（遷移前後比對）
for d in "$STORE"/*/; do printf '%s %s\n' "$(find "$d" -type f | wc -l)" "$d"; done
find "$VAULT" -type f | wc -l
systemctl --user show obsidian-sync.service -p MemoryCurrent -p MemoryPeak
hippo doctor
```

### 1. 停止所有寫入者

```bash
systemctl --user stop paulsha-hippo-dream.timer paulsha-hippo-dream.service
systemctl --user stop obsidian-sync-healthcheck.timer obsidian-sync.service   # healthcheck 會自動拉起 sync，一併停
# 結束正在跑的 agent CLI session（hooks 會寫 inbox／runtime），必要時一併停 cortex 等外部寫入者
pgrep -af 'paulsha_hippo|hippo ' || echo "無 hippo 進程"
flock -n ~/.agents/memory/runtime/locks/dream.lock true && echo "dream lock 空閒"
```

### 2. 搬移並翻轉 symlink

```bash
mv ~/.agents/memory ~/.agents/memory.vault-link      # 保留舊 symlink 供回滾（只改 symlink 名稱）
mkdir ~/.agents/memory
for entry in "$STORE"/* "$STORE"/.[!.]*; do
  [ -e "$entry" ] || continue
  [ "$(basename "$entry")" = knowledge ] && continue
  mv "$entry" ~/.agents/memory/
done
ln -s "$STORE/knowledge" ~/.agents/memory/knowledge
```

### 3. 驗證

```bash
readlink -f ~/.agents/memory/archive                  # 不得落在 $VAULT 底下
ls -A "$STORE"                                          # 只剩 knowledge
for d in ~/.agents/memory/*/; do printf '%s %s\n' "$(find -L "$d" -type f | wc -l)" "$d"; done   # 與基線一致
hippo doctor                                            # storage 位置：✓，knowledge 借回 vault
hippo dream status --memory-root ~/.agents/memory
hippo dream run --dry-run --memory-root ~/.agents/memory
```

### 4. 恢復服務與觀察

```bash
systemctl --user start obsidian-sync.service obsidian-sync-healthcheck.timer
systemctl --user start paulsha-hippo-dream.timer
find "$VAULT" -type f | wc -l                            # 掃描面積應大幅下降
systemctl --user show obsidian-sync.service -p MemoryCurrent -p MemoryPeak   # 觀察數個整點
```

驗收：連續數個整點 dream 觸發後，同步程序穩態記憶體明顯下降，且 hippo 讀寫與 knowledge 在 Obsidian 的同步都正常。確認穩定後再刪除 `~/.agents/memory.vault-link`。

### 回滾

```bash
# 先照步驟 1 停止所有寫入者
STORE="$(readlink -f ~/.agents/memory.vault-link)"     # 由保留的舊 symlink 取回原位置
rm ~/.agents/memory/knowledge                          # 只移除 symlink
for entry in ~/.agents/memory/* ~/.agents/memory/.[!.]*; do
  [ -e "$entry" ] || continue
  mv "$entry" "$STORE"/
done
rmdir ~/.agents/memory
mv ~/.agents/memory.vault-link ~/.agents/memory
readlink -f ~/.agents/memory                            # 回到 $STORE
# 再照步驟 3、4 驗證並恢復服務
```

## archive GC

`archive/` 底下有三個子樹，會無界成長：

| 子樹 | 內容 | GC |
|---|---|---|
| `archive/sessions/` | split 後搬離 inbox 的 session 文件（atomizer 衍生副本） | 對應 session 已落成即可回收 |
| `archive/fragments/` | promote 後搬離 `_slices` 的 fragment（atomizer 衍生副本） | 對應 session 已落成即可回收 |
| `archive/queue/` | importer 截取的 raw capture payload | **一律保留** |

`archive/sessions` 與 `archive/fragments` 在程式內沒有任何讀取端，且可由 raw capture 重建；`archive/queue` 則是 importer 的 frozen raw 來源，無法重建，並被下列既有流程直接讀取，刪除任何一份都會打斷它們：

- `hippo recovery plan|apply|resume|rollback`：plan 以 `archive/queue/*.json` 為來源並把每份 source 的 hash 釘進 manifest；apply／resume／rollback 驗 pin 時缺任何一份即 `source pin drift`，`--source-manifest` 重新規劃時缺檔即 `source authority file is missing`。
- `python -m paulsha_hippo.importer.backfill`：從 `archive/queue` 重新 extract inbox 內容。
- `hippo knowledge backfill-provenance` 與 janitor 的 `check_provenance_path`：透過 knowledge 的 `provenance.path` 讀／驗 queue payload。

`hippo archive gc` 以 hippo 既有的處理紀錄為準，回收「對應 session 已落成 knowledge」的衍生副本：

```bash
hippo archive gc --memory-root ~/.agents/memory --list-out /tmp/archive-gc.tsv      # 預設 dry-run
hippo archive gc --memory-root ~/.agents/memory --apply --list-out /tmp/archive-gc-applied.tsv
```

| 旗標 | 說明 |
|---|---|
| （預設）／`--dry-run` | 只輸出 JSON 統計與刪除清單，不寫 memory root 內任何檔案 |
| `--apply` | 實際刪除；須取得 dream global lock（`dream run` 進行中即拒絕），在持鎖下重新規劃後才刪 |
| `--retention-days N` | 安全保留窗（預設 7）：session 落成時間**或**檔案 mtime 未滿 N 天者保留 |
| `--include-no-findings` | 把 `no-findings`（已蒸餾但未產出 knowledge）的 session 也納入；預設只收 `promoted` |
| `--list-out FILE` | 刪除清單寫入檔案（每行 `<相對路徑>\t<bytes>\t<session_key>`）；未給時清單列在 JSON 的 `candidate_paths` |
| `--now ISO8601` | 固定「現在」時間（測試／重現用） |

歸因方式：

- session 是否落成：`runtime/ledger/processing.jsonl` fold 後的最新狀態。
- `archive/sessions`、`archive/fragments`：依 atomizer 固定命名對回 session，刪除前再以檔內 frontmatter 的 `source_agent`／`source_session` 複驗。

一律保留（報告 `kept.by_reason` 會列出各原因的檔數與 bytes）：

| 原因 | 意義 |
|---|---|
| `raw-queue-retained` | `archive/queue` 下的 raw capture（見上：recovery／backfill 的來源） |
| `unattributable` | 無法歸因：命名不符、processing ledger 查無此 session、或多個 session 撞同一前綴 |
| `not-landed:<state>` | 尚未落成：`split`、`parked`、`quarantined`、`no-findings`（未加旗標時）等 |
| `pending-inbox` | inbox 仍有該 session 的文件或 `_slices` fragment（可能待重新蒸餾） |
| `provenance-pinned` | 被 knowledge／inbox 的 `provenance.path` 引用（不論子樹的防線）；刪除會讓 provenance 懸空，janitor `check_provenance_path` 會把該 knowledge 誤判 `source_invalid` |
| `retention-window` | 仍在保留窗內 |
| `unknown-landed-at` | 落成事件時間戳無法解析 |
| `attribution-mismatch` | 檔名歸屬與 frontmatter 不一致 |
| `not-regular-file`／`unexpected-layout` | symlink、目錄或非 `archive/<子樹>/<月份>/<檔>` 層級的項目 |

安全性：`--list-out` 寫不出來時回報 JSON `error`、exit 1，且一檔不刪；`archive` 或其子樹是 symlink 時拒絕 `--apply`；刪除以 dir fd 逐層 `O_NOFOLLOW` 開啟，逐檔核對 inode／大小與規劃時一致才 unlink；只刪檔、不刪目錄。重跑是冪等的（已刪的檔不會再出現在清單）。`--apply` 回報含 `error`、`blocked` 或 `failed` 時 exit 1。

回滾：刪除不可逆。需要保底時，在 `--apply` 前用 dry-run 產出的清單先打包：

```bash
hippo archive gc --memory-root ~/.agents/memory --list-out /tmp/archive-gc.tsv
tar -C ~/.agents/memory -czf /tmp/archive-gc-backup.tgz -T <(cut -f1 /tmp/archive-gc.tsv)
hippo archive gc --memory-root ~/.agents/memory --apply --list-out /tmp/archive-gc-applied.tsv
# 還原：tar -C ~/.agents/memory -xzf /tmp/archive-gc-backup.tgz
```

建議順序：先完成上面的 store 遷移，再跑 GC（在 vault 內大量刪檔同樣會讓同步程序 churn）。

已知限制：

- `archive/queue` 不回收。若要回收已落成 session 的 raw capture，需先設計 recovery 交易的生命週期（哪些 manifest 已結案、可放棄重跑）與 provenance 改寫，另開議題處理。
- `runtime/`（例如 recovery 交易快照）不在本指令範圍。
- 本指令尚未掛進 dream timer；定期執行前請先以 dry-run 核對幾輪結果。
