# stage2-noise-governance Specification

## Purpose
定義 knowledge 噪音的判準（classifier）、產生端過濾、回溯 prune 與稽核 manifest，於產生端阻斷結構/空殼/placeholder slice、並可回溯 hard-delete 既有噪音，同時避免誤刪真實知識。
## Requirements
### Requirement: 以 body 內容判定 noise

系統 SHALL 提供純函式 `classify_noise(frontmatter, body)`，回傳 `(is_noise, reason)`，且判定**僅依 body 內容**，不依賴 frontmatter 的 `atom_title` / `title` / `project`。判定規則依序為：(1) **structural-echo**——(a) body 第一行為 importer-exclusive 結構 heading（`## CWD` / `## Source` / `## Prompts` / `## Touched files` / `## Referenced artifacts`）或 session metadata 區塊（`#{1,6} Session Metadata|Information`）→ **無條件** echo（這些段落名永遠不是合法的獨立知識原子標題）；(b) body 第一行為 `## Summary`（真筆記常見）→ 僅當散文行（非標題、非清單）≤1 時才 echo；(2) body **開頭附近**（前 12 字內）含 `尚未收到您的具體需求` / `目前尚未收到` / `(無內容)`，或本體僅 `- (none)` / `(unknown)` → `placeholder`；(3) body 去除標題行（`#`..）與空白行後無實質內容（涵蓋純標題片段如 `# Session <uuid>` 與真正空白）→ `empty`；皆不命中則非 noise。判定 SHALL 為 content-based，MUST NOT 以字元長度門檻判定。因 classifier 同時用於 hard-delete 路徑，規則 SHALL 為 deletion-grade：`## Summary` 的 ≤1 散文行 guard 與 placeholder 的開頭偵測，避免誤刪「以 `## Summary` 開頭但有多段真內容」或「引用 placeholder 字串」的真筆記。

#### Scenario: importer-exclusive 結構段落無條件判為 noise

- **WHEN** slice body 第一行為 `## CWD`、`## Prompts`、`## Source`、`## Touched files`、`## Referenced artifacts`，或為 `Session Metadata` / `Session Information` 區塊（不論其後內容多寡）
- **THEN** `classify_noise` SHALL 回 `is_noise=True` 且 `reason` 為 `structural-echo:<section>`

#### Scenario: `## Summary` 僅在淺內容時判為 noise

- **WHEN** slice body 第一行為 `## Summary` 且散文行 ≤1
- **THEN** `classify_noise` SHALL 回 `is_noise=True` 且 `reason` 為 `structural-echo:Summary`

#### Scenario: 純標題片段與 placeholder 判為 noise

- **WHEN** slice body 去除標題行與空白後無實質內容（如 `# Session <uuid>`），或開頭附近含 placeholder 字串
- **THEN** `classify_noise` SHALL 回 `is_noise=True` 且 `reason` 為 `empty` 或 `placeholder`

#### Scenario: 真實短內容不因長度被誤判

- **WHEN** slice body 為非標題、非 placeholder 的真實短句（例如 30 字的技術結論）
- **THEN** `classify_noise` SHALL 回 `is_noise=False`（判定 MUST NOT 依字元長度門檻）

#### Scenario: 以 `## Summary` 開頭但有多段真內容者保留

- **WHEN** slice body 第一行為 `## Summary`，但其後含 ≥2 行真實散文
- **THEN** `classify_noise` SHALL 回 `is_noise=False`（非 structural-echo，防 hard-delete 誤刪）

#### Scenario: 引用 placeholder 字串的真筆記保留

- **WHEN** slice body 為真內容，僅在中段引用 placeholder 字串（非開頭）
- **THEN** `classify_noise` SHALL 回 `is_noise=False`

#### Scenario: 有真內容者保留（精度防誤刪）

- **WHEN** slice 的 `atom_title` 為 `untitled` 或 `project` 為 username fallback，但 body 含足夠真實內容（例如 checklist、技術結論）
- **THEN** `classify_noise` SHALL 回 `is_noise=False`

### Requirement: 產生端過濾防止噪音新生

atomize 的 promote pass SHALL 在 slice 通過 frontmatter 驗證後、寫入 knowledge 前，對每個 slice 套用 `classify_noise`；判為 noise 者 MUST NOT 寫入 knowledge 層、MUST NOT 建立 semantic relation，並 SHALL 計入結果 summary 的 `noise_dropped`。source fragment 的 archive 與 session 的 promoted 標記 SHALL 不受影響。預期內的 noise 丟棄 MUST NOT 計入健康訊號（`warnings` / summary `skipped`），以免正常的過濾被誤判為 degraded（partial）；診斷細節改以 log 記錄。

#### Scenario: noise slice 被產生端丟棄

- **WHEN** promoter 對某 session 產出一個 body 為結構段落 echo 的 slice
- **THEN** 該 slice MUST NOT 出現在 knowledge 層，且 summary `noise_dropped` SHALL 反映被丟棄數，source fragment SHALL 仍被 archive

#### Scenario: noise 丟棄不汙染健康訊號

- **WHEN** 某 run 僅因 noise 過濾而丟棄 slice、無其他異常
- **THEN** summary `skipped` SHALL 為 0 且 `warnings` MUST NOT 含 noise 丟棄條目（使 dream 健康判定為 clean）

#### Scenario: 乾淨 slice 正常寫入

- **WHEN** promoter 產出 body 為真實萃取內容的 slice
- **THEN** 該 slice SHALL 正常寫入 knowledge 並建立其 relation

### Requirement: 回溯 prune 既有噪音

系統 SHALL 提供 `psc memory knowledge prune-noise` 子命令掃描 `knowledge/**.md`（排除 `*-moc.md`），對每檔以 `classify_noise` 判定。預設 `--dry-run` MUST NOT 修改任何檔，僅列出將刪清單與 reason 統計。`--apply` SHALL hard delete 命中檔並重建 MOC。命令 SHALL 一律輸出稽核 manifest 至 `runtime/ledger/prune-<now>.jsonl`（每行含 slice_id / project / path / reason / status）。`--apply` SHALL 先持久化「planned」manifest 後才執行任何 unlink，刪除完成再以 atomic replace 重寫最終狀態；確保任何後續寫入失敗都不致留下無紀錄的刪除。

#### Scenario: dry-run 不動檔

- **WHEN** 執行 `prune-noise --dry-run`
- **THEN** 命令 SHALL 列出將刪 slice 與 reason 統計，且 knowledge 層任何檔 MUST NOT 被刪除或修改

#### Scenario: apply 刪噪音並保留乾淨 slice

- **WHEN** 執行 `prune-noise --apply`，knowledge 同時含噪音與乾淨 slice
- **THEN** 命中 noise 的檔 SHALL 被 hard delete、乾淨 slice SHALL 全數保留、manifest SHALL 記錄每筆刪除、MOC SHALL 重建且不含已刪 slice

#### Scenario: 刪除前已有 durable manifest

- **WHEN** `prune-noise --apply` 開始刪除任一檔
- **THEN** 該次的 planned manifest SHALL 已先寫入 `runtime/ledger/`，使任一後續失敗都不致留下無稽核紀錄的刪除

#### Scenario: 單檔刪除失敗不中止整體

- **WHEN** `--apply` 過程中某檔刪除失敗
- **THEN** 該筆 SHALL 以 `status=error` 記入 manifest、命令 SHALL 跳過該檔並續處理其餘檔

### Requirement: 以 instruction 文件逐字語料判定 doc-fragment

系統 SHALL 擴充 `classify_noise(frontmatter, body)`，新增選用參數 `doc_corpus`（agent-instruction 文件——CLAUDE.md / AGENTS.md / GEMINI.md——的逐字參照語料，含 heading 集合與內容行集合）。當提供非空 `doc_corpus` 時，若 body strip 後第一行為 markdown heading 且其 heading 文字逐字命中語料 heading 集合，**且** body 去標題後 ≥2 條內容行逐字命中語料行集合，則判為 noise、`reason` 為 `doc-fragment`。判定 SHALL 為逐字（normalized whitespace）比對（deletion-grade，只命中可證實為 instruction 文件片段者），MUST NOT 僅憑「heading 為編號章節」此單一結構特徵判定。未提供 `doc_corpus`（或語料為空）時，doc-fragment 規則 SHALL 不啟用，既有判定行為不變。

#### Scenario: instruction 文件編號章節碎片判為 doc-fragment

- **WHEN** slice body 第一行為 `## 6. 自主維護規則（agent-managed）` 等 instruction 文件章節 heading，後續 ≥2 內容行為該文件逐字內容，且提供涵蓋該文件的 `doc_corpus`
- **THEN** `classify_noise` SHALL 回 `is_noise=True` 且 `reason` 為 `doc-fragment`

#### Scenario: instruction 文件非編號段落碎片亦判為 doc-fragment

- **WHEN** slice body 第一行為 `## 動工前` 等 AGENTS.md 段落 heading，後續內容行逐字命中語料，且提供對應 `doc_corpus`
- **THEN** `classify_noise` SHALL 回 `is_noise=True` 且 `reason` 為 `doc-fragment`

#### Scenario: 真知識的編號小節不被誤刪

- **WHEN** slice body 第一行為 `## 1. 背景` 等編號 heading，但內容為原創知識、未逐字命中任何 instruction 語料
- **THEN** `classify_noise` SHALL 回 `is_noise=False`（編號章節單一特徵 MUST NOT 致刪）

#### Scenario: 未提供語料時規則惰性

- **WHEN** 呼叫 `classify_noise(fm, body)` 未傳 `doc_corpus`（或語料為空），body 為任意 doc 碎片
- **THEN** doc-fragment 規則 SHALL 不啟用，回傳結果與既有（structural-echo / placeholder / empty）判定一致

### Requirement: 產生端與回溯 prune 共用 doc-fragment 判準

atomize 的 promote pass 與 `psc memory knowledge prune-noise` SHALL 自 instruction-doc 語料來源組裝 `doc_corpus` 並傳入 `classify_noise`，使 doc-fragment 於產生端被阻斷新生、於回溯 prune 被清除。語料探測 SHALL 邊界化（限定安全 root、限深、skip 重目錄），探測不到語料時 doc-fragment 規則惰性而不致誤刪。

`memory dream run` SHALL 提供 opt-in、repeatable 的 `--instruction-root` 參數（語意與 `memory atomize` 既有參數一致），並以 `corpus_for_roots` 組裝 `doc_corpus` 傳入 atomize pass，使 doc-fragment 規則在 dream 生產路徑生效、drop 計入 atomize summary 的 `noise_dropped`（隨 dream ledger 記錄）。未傳 `--instruction-root` 時 SHALL 組出空語料、doc-fragment 規則惰性，dream run 行為 MUST 與既有行為完全一致（行為契約）。生產 dream loop（`scripts/start.sh`）SHALL 對 `memory dream run` 傳入 instruction-doc 語料 roots，其來源 SHALL 與檢索 index 端 pool-exclude 所用的 curated default roots（`instruction_corpus.default_roots()`）一致，使產生端 drop 與 index 端排除的判定語料相同。

#### Scenario: 回溯 prune 清除 doc-fragment

- **WHEN** 對含 instruction 文件碎片與真知識的 knowledge 執行 `prune-noise --apply`，且語料涵蓋該 instruction 文件
- **THEN** doc-fragment 碎片 SHALL 被 hard delete、真知識 SHALL 全數保留、manifest SHALL 記錄每筆刪除（reason `doc-fragment`）、MOC SHALL 重建且不含已刪 slice

#### Scenario: 產生端阻斷 doc-fragment 新生

- **WHEN** promoter 對某 session 產出一個 body 為 instruction 文件逐字章節的 slice，且產生端已組裝涵蓋該文件的語料
- **THEN** 該 slice MUST NOT 寫入 knowledge 層、SHALL 計入 `noise_dropped`，source fragment SHALL 仍被 archive

#### Scenario: dream run 帶 --instruction-root 時 doc-fragment 於 dream 路徑被 drop

- **WHEN** 執行 `memory dream run --instruction-root <doc>`，inbox 含一個 body 為該 instruction 文件逐字段落（heading 命中 + ≥2 內容行逐字命中）的 session 與一個真知識 session
- **THEN** doc-fragment slice MUST NOT 寫入 knowledge 層、dream 結果 `passes.atomize.noise_dropped` SHALL 計入該筆，真知識 slice SHALL 照常寫入 knowledge

#### Scenario: dream run 不帶 --instruction-root 時行為不變

- **WHEN** 執行 `memory dream run`（未傳 `--instruction-root`），inbox 含 body 為 instruction 文件逐字段落的 session
- **THEN** doc-fragment 規則 SHALL 惰性（`noise_dropped` 不因該 slice 增加）、該 slice SHALL 照既有行為寫入 knowledge——與變更前行為完全一致

#### Scenario: 生產 dream loop 傳入語料 roots

- **WHEN** 檢視 `scripts/start.sh` 的 dream loop 對 `memory dream run` 的呼叫
- **THEN** 該命令 SHALL 帶 `--instruction-root` 參數，且 roots 集合 SHALL 與 `instruction_corpus.default_roots()` 一致

### Requirement: 檢索 index 與 brief 對 noise 的 defense-in-depth 排除

`build_index`（`paulshaclaw/memory/moc/search.py`）建立 `retrieval.db` 時、以及 SessionStart slim brief 納入 slice 時，SHALL 對候選 slice 套用既有 `classify_noise`（含 instruction-doc `doc_corpus`，沿用本 spec 既有判準），命中者 MUST NOT 被索引、MUST NOT 進入 brief，使**尚未被 `prune-noise` 清除的殘留噪音**也不會出現在 prompt-retrieval 短清單。此排除為純讀取側、MUST NOT hard-delete 任何檔（刪除仍走既有 `prune-noise` 路徑）。乾淨 slice SHALL 照常索引與檢索。

#### Scenario: 殘留 doc-fragment 不進檢索 index
- **WHEN** knowledge 仍含未經 prune 的 doc-fragment slice，且重建 `retrieval.db`
- **THEN** 該 slice MUST NOT 出現在 `slices_fts`，prompt-retrieval 檢索 MUST NOT 將其列入短清單，且該檔 SHALL 仍存在於 knowledge（未被刪）

#### Scenario: 乾淨 slice 正常索引
- **WHEN** 重建 index 時某 slice 經 `classify_noise` 判為非 noise
- **THEN** 該 slice SHALL 被索引並可被檢索命中

### Requirement: canary/review 類非刪除級池排除

offered pool（檢索 index 與 brief / 短清單）SHALL 排除 canary/smoke fixture 與一次性 PR/adversarial review-record 類 slice（依 `artifact_kind` 或既知標記辨識）。此排除為**池端、非刪除級**：辨識門檻得較 `classify_noise` 寬鬆（因不觸發 hard-delete），且 MUST NOT 刪除該類檔（保留於 knowledge 供稽核）。

#### Scenario: canary fixture 不進短清單但保留在 knowledge
- **WHEN** knowledge 含一個 canary/smoke fixture slice
- **THEN** prompt-retrieval 短清單與 brief MUST NOT 列入該 slice，且該檔 SHALL 仍保留在 knowledge 層

#### Scenario: review-record 不進短清單
- **WHEN** knowledge 含一筆一次性 PR review-record slice
- **THEN** 檢索/短清單 MUST NOT 將其列出

### Requirement: generic 標題 artifact 之非刪除級池排除

系統 SHALL 提供 title 級純函式 `is_generic_title(title)`：將 title 正規化（小寫、空白與底線折成 `-`）後，恰為 `overview` / `problem` / `untitled` / `review-summary` / `report` / `task` / `todo`，或以 `report-` / `task-` / `todo-` 為字首者，判為 generic 標題（session 產物型標籤、非知識原子）。`pool_exclude_reason(frontmatter)` SHALL 於 `atom_title` 或 `title` 命中 generic 標題時回傳 `generic-title`，使該 slice 於 `build_index` 重建檢索池時被排除（不進 offered pool / 短清單）。此排除為**池端、非刪除級**：MUST NOT 刪除或修改該檔（保留於 knowledge，供 retitle 重生標題後回池）。`session_title` 為 generic MUST NOT 觸發排除（session 標題非 slice 標題）。僅「包含」generic 詞而非恰為或字首命中者（如 `overview-of-uart-pinmux`、`problem-with-dma-burst`）MUST NOT 判為 generic。空字串或缺失 title MUST NOT 判為 generic（留給既有 untitled/retitle 治理）。

#### Scenario: generic 標題 slice 不進檢索 index

- **WHEN** knowledge 含一筆 `title: report-testpilot` 的 slice 與一筆具體標題的 slice，且重建 `retrieval.db`
- **THEN** generic slice MUST NOT 出現在 `slices_fts`（檢索 MUST NOT 命中），具體標題 slice SHALL 照常索引，且 generic 檔 SHALL 仍存在於 knowledge（未被刪除或修改）

#### Scenario: 恰為 generic 名稱者命中、僅包含者不命中

- **WHEN** 以 `overview`、`Review Summary`、`todo_cleanup`、`task-cockpit-swap` 呼叫 `is_generic_title`
- **THEN** 回傳值 SHALL 皆為 True
- **WHEN** 以 `overview-of-uart-pinmux`、`problem-with-dma-burst`、`單一-com0-死因未解`、空字串呼叫 `is_generic_title`
- **THEN** 回傳值 SHALL 皆為 False

#### Scenario: session_title generic 不致排除

- **WHEN** 某 slice 的 `title` 為具體標題、`session_title` 為 `report-testpilot`
- **THEN** `pool_exclude_reason` SHALL 回傳 None（該 slice 照常入池）

#### Scenario: retitle 後回池

- **WHEN** 某 generic 標題 slice 經 retitle 重生為具體標題，且之後重建 `retrieval.db`
- **THEN** 該 slice SHALL 重新被索引並可被檢索命中

