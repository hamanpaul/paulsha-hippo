# Push shortlist 雜訊基準線與確定性收窄 shadow 量測

> 對應 issue #158（#148 的 H1）。H1 假設：減少未經請求的記憶注入（push），可以大幅降低
> 雜訊與 token，而不增加可觀測的失敗。本文件定義驗證 H1 所需的三件工具與其契約：
>
> 1. **push shadow**：prompt hook 照舊注入，另外計算「確定性收窄後」的 shortlist，寫進獨立的 shadow ledger。
> 2. **凍結 query 集格式**：每個 query 附 BM25 top-12 候選、分數、相關性標註、標註者與方法。
> 3. **`hippo shortlist eval`**：在凍結 query 集上輸出 Precision@3、Noise@3、Relevant-missed@12 與注入字元量，並提供門檻校準。
>
> 非目標：本票**不**實際收窄 production 注入（要等 shadow 結果）；不接任何語意 judge 或 JEV；
> 不改 recall／retrieval 的排序演算法（#117 另計）。

## 1. Push shadow

### 1.1 設定

設定放在 canonical config `~/.config/paulsha-hippo/config.yaml`（`HIPPO_CONFIG_ROOT` 可覆寫目錄）的 `shortlist.push_shadow`。
全部鍵皆 optional，缺鍵或值不合法時**逐鍵**退回預設，不會讓 hook 失敗。

```yaml
shortlist:
  push_shadow:
    enabled: false        # 預設關閉
    bm25_min_score: 0.0   # 收窄門檻：保留 score = -bm25 >= 此值者
    max_k: 1              # 收窄後最多幾則（0–3）
    time_budget_ms: 50    # shadow 計算上限（毫秒）
```

| 鍵 | 型別／範圍 | 預設 | 說明 |
|---|---|---|---|
| `enabled` | bool | `false` | 開啟後才計算與寫入 shadow；注入內容不受影響。 |
| `bm25_min_score` | 數值，`>= 0`、有限 | `0.0` | SQLite FTS5 `bm25()` 越負越相關；Hippo 一律以 `score = -bm25`（越大越相關）比較。`0` 代表未校準、不設分數門檻。 |
| `max_k` | 整數 `0–3` | `1` | 收窄後最多保留幾則。`0` 等於「完全不 push」的反事實。上限 3 是因為收窄只從現行 claim（每次最多 3 則）內取。 |
| `time_budget_ms` | 數值 `(0, 1000]` | `50` | shadow 計算（不含最後一次寫入）超過此值即丟棄該筆紀錄並記 `hooks.log` warning。 |

預設值的理由：

- `bm25_min_score: 0.0`——FTS5 bm25 的絕對值隨 query token 數、語料規模與詞頻浮動，沒有凍結樣本就沒有可辯護的數字，因此預設不設門檻；門檻一律依第 4 節由凍結樣本校準後再寫入設定。
- `max_k: 1`——未校準前最小的非零推送量，讓 shadow 一開啟就能量到「只推最相關一則」相對現況的字元量差距。每筆 shadow 事件都保存收窄前每則的 bm25，離線可用任何 `(門檻, max_k)` 重算，不必為了換參數重新蒐集。
- `time_budget_ms: 50`——shadow 只做 O(k) 的記憶體計算；在合成資料上實測 shadow 計算約 0.1 ms 以內，50 ms 是遠高於正常值、但仍遠低於 hook 逾時的保險絲。

### 1.2 行為保證

- **注入逐位元相同**：shadow 只讀取現行管線已經算好的 hits、claim、已 redact 的 block 與 applied 指引，不修改任何一個，也在 offered commit point 之後、per-session 鎖釋放之後才執行。測試以同一 memory root 分別在關閉／開啟下跑相同 prompt 序列，比對 hook 回傳字串、`claude_user_prompt_submit.py`／`copilot_user_prompt_submit.py` 真實進程的 stdout 位元組、offered ledger 與 per-session map，皆相同（`tests/test_push_shadow.py`）。
- **不呼叫外部 LLM**：shadow 不重跑檢索（沿用同一次 `search()` 結果）、不 spawn 子行程、不開網路。測試在開啟 shadow 時把 `subprocess`／`socket` 換成會失敗的替身，管線仍正常注入並寫入 shadow。
- **best-effort**：shadow 的任何例外（組事件、寫檔失敗、目錄不可寫……）只記 `<memory_root>/log/hooks.log`，注入照常；呼叫端另有一層 try，確保 shadow 不會讓已 commit 的注入被外層 fail-closed 吞成空字串。
- **延遲上限**：shadow 額外成本＝O(k) 計算＋一次 `O_APPEND` 單行寫入（無鎖、無 fsync、無額外檢索）。計算超過 `time_budget_ms` 即丟棄該筆紀錄，因此最壞額外延遲被限制在「預算＋單次 append」。
- **只量 push**：只有兩條 UserPromptSubmit 自動 hook 會記 shadow；`hippo recall`（使用者主動拉取）不記。
- **只在真的有注入時記錄**：slash command、無 project、無命中、早停、全數已 offer 等「現況不注入」的 prompt 不寫 shadow（before 與 after 都是空）。

### 1.3 Shadow ledger

位置：`<memory_root>/runtime/ledger/push_shadow.jsonl`（append-only JSONL，與 `offered.jsonl` 分開；`hippo ledger repair` 的撕裂行掃描同樣涵蓋此檔）。每行一筆事件：

| 欄位 | 說明 |
|---|---|
| `schema` | 事件 schema 版本（目前 `1`）。 |
| `ts` | UTC ISO8601。 |
| `session_id`／`tool`／`project` | 與同一次 offered 事件相同的歸因，可直接 join `offered.jsonl`、`memory_usage.jsonl`。 |
| `query_sha256` | FTS 淨化後 query 的 sha256。**不記 prompt 或 query 原文**（shadow ledger 不經 redaction boundary）。 |
| `query_terms` | FTS query 的 token 數；BM25 絕對值與它高度相關，校準時用來分層。 |
| `params` | 本筆使用的 `{bm25_min_score, max_k}`。 |
| `candidates` | 本次檢索過取的候選數（≤ 12）。 |
| `before` | 現況實際注入的 claim，依注入順序：`{sl_id, bm25, rank}`；`rank` 為該則在檢索結果中的 0-based 名次。 |
| `after` | 收窄後會注入的 `sl_id`（`before` 的子序列）。 |
| `dropped` | `before` 中被收窄掉的 `sl_id`。 |
| `chars_before` | 現況實際注入的字元數（= hook 回傳字串長度）。 |
| `chars_after` | 若只注入 `after` 時的字元數；`after` 為空時為 `0`。 |
| `chars_exact` | `true` 表示 `chars_after` 是從已 redact 的 block 結構化切出提示行與被保留的列（依未 redact 排版中各段的換行數對齊，標題含換行、整列被 redaction 替換時仍精確），與「現行管線只注入這幾則」逐字元相同；只有對不上時（例如 redaction 行為改變）才退回未 redact 排版估算並標 `false`。 |
| `pipeline_ms` | 從進入 shortlist 管線到注入字串完成的毫秒數（現況延遲的觀測值）。 |
| `shadow_ms` | shadow 本身計算的毫秒數。 |

### 1.4 已知限制

- **逐事件、非整段 session 模擬**：收窄只作用在「當次實際注入」的 claim 上；session 內去重仍跟著真實注入走。真實收窄後，被收窄掉的 note 不會進 seen，之後的 prompt 可能再次出現。shadow 因此是收窄效益的**樂觀估計**。
- 同主題折疊（`collapse_same_topic`）、早停、去重都已套用在 `before` 上；shadow 不重新評估它們。
- `chars_*` 含固定開銷（提示行＋applied 指引，含 show 指令與 memory root 路徑）。在合成資料上一則的注入約是三則的六成以上，收窄則數帶來的字元節省會被固定開銷稀釋——這是 H1 評估時要一併看的數字。

### 1.5 部署後開啟 shadow

1. 確認部署版本已含本功能（`hippo --version`，並重新 `hippo install hooks` 讓 hook 腳本與套件一致）。
2. 編輯 `~/.config/paulsha-hippo/config.yaml`，在既有 `shortlist:` 下加入 `push_shadow:` 區塊，將 `enabled` 設為 `true`（其餘鍵可先用預設）。config 是 create-only，升級不會自動補鍵，需手動加入（鍵名與預設值見 1.1，或參考套件內模板 `paulsha_hippo/atomizer/atomizer.yaml` 的 `shortlist.push_shadow`）。
3. 設定每個 prompt 都會重讀，不需重啟服務。正常使用幾個 prompt 後確認 `<memory_root>/runtime/ledger/push_shadow.jsonl` 開始成長、`<memory_root>/log/hooks.log` 沒有 `push shadow` warning。
4. 關閉：把 `enabled` 改回 `false`。shadow ledger 可直接保留或移走，其他 ledger 不依賴它。

### 1.6 分析 shadow ledger 的起點（唯讀）

```bash
L=<memory_root>/runtime/ledger
# 總注入字元：現況 vs 收窄
jq -s '{events: length,
        chars_before: (map(.chars_before) | add),
        chars_after: (map(.chars_after) | add)}' "$L/push_shadow.jsonl"

# 可觀測失敗的候選：被收窄掉、但同一 session 內被讀取或回報 applied 的 note
jq -s '[.[] | .dropped[] as $d | {session_id, tool, sl_id: $d}] | unique' "$L/push_shadow.jsonl" > dropped.json
jq -c 'select(.source == "read" or .kind == "applied")
       | {session_id, tool, sl_id: (.sl_id // .slice_id)}' "$L/memory_usage.jsonl" \
  | jq -s --slurpfile d dropped.json '[.[] | select(. as $r | $d[0] | index($r))] | unique'
```

每筆事件都有 `before[].bm25`，要評估其他 `(門檻, max_k)` 時，對 `before` 重新套用「依序保留 `-bm25 >= 門檻`、最多 `max_k` 則」即可，不需重新蒐集。

## 2. 凍結 query 集格式

凍結 query 集是一個 UTF-8 JSON 物件。**真實樣本含真實 prompt 與 note 標題，只能放在私有位置，不得提交進本 repo**；repo 內只有合成範例 `tests/fixtures/shortlist_eval/synthetic_frozen_queries.json`。

頂層欄位：

| 欄位 | 必填 | 說明 |
|---|---|---|
| `format` | ✔ | 固定 `"hippo-shortlist-frozen-queries"`。 |
| `version` | ✔ | 固定 `1`。 |
| `name` | | 樣本名稱（報表顯示用）。 |
| `annotator` | 條件 | 預設標註者；每個 query 可覆寫。每個 query 最終都必須解析出非空的標註者。 |
| `method` | 條件 | 預設標註方法（標準、流程、是否盲標等）；每個 query 可覆寫，規則同上。 |
| `block_overhead_chars` | | 非負整數，預設 `0`。注入非空時額外計入的固定字元（提示行＋applied 指引）。 |
| `queries` | ✔ | 非空陣列。 |
| `block_header`／`applied_hint` | | `freeze` 產生：注入的提示行（已 redact）與 applied 指引原文；`block_overhead_chars` 即兩者長度加一個換行。 |
| 其他 | | `description`、`project`、`frozen_at`、`fetch_k`、`baseline_k`、`collapse_same_topic` 等為說明用途，評估時忽略。 |

`queries[]` 欄位：

| 欄位 | 必填 | 說明 |
|---|---|---|
| `id` | ✔ | 樣本內唯一。 |
| `query` | ✔ | 查詢文字（非空）。 |
| `candidates` | ✔ | BM25 top-12 經 hook 同一條候選路徑（同主題折疊、去除無 slice_id）後的候選，**依 hook 的注入順序**排列，0–12 筆；前 3 筆即現況 push 對全新 session 會注入的內容，0 筆代表現況不會注入。 |
| `annotator`／`method` | | 覆寫頂層值。 |
| `fts_query` | | `freeze` 產生的 FTS 淨化結果，說明用途。 |
| `collapsed` | | `freeze` 記錄的同主題折疊對照（`{保留的 id: [被折疊的 id…]}`），被折疊者不在 `candidates` 內；稽核用途。 |

`candidates[]` 欄位：

| 欄位 | 必填 | 說明 |
|---|---|---|
| `note_id` | ✔ | note 的 `slice_id`，同一 query 內唯一。 |
| `bm25` | ✔ | 原始 FTS5 `bm25()`（負值，越負越相關），有限數值。 |
| `relevant` | ✔ | `true`／`false`。`null`（未標註）會被 `eval` 拒絕。 |
| `chars` | ✔ | 非負整數：這一則在注入中佔的字元數（該列＋換行）。 |
| `row` | | `freeze` 產生：這一則注入時的列文字（已 redact，不含前導換行）；`chars` = 1 + 它的長度。標註時可直接看到 agent 實際看到的內容。 |
| `title`／`path` | | 標註時參考用，評估時忽略。 |

## 3. `hippo shortlist eval`

```bash
hippo shortlist eval --queries <frozen.json> [--min-score X] [--max-k N] [--sweep auto|X1,X2,...] [--json]
```

- **baseline**：依凍結排序取前 3 則（現行 push 每次最多 3 則）。
- **narrowed**：在 baseline 內依序保留 `-bm25 >= --min-score` 者、最多 `--max-k` 則——與線上 shadow 共用同一個函式（`paulsha_hippo.push_shadow.narrowed_indices`）。
- `--min-score` 預設 `0`、`--max-k` 預設 `1`（與 shadow 預設相同）；**不讀 runtime config**，結果只取決於輸入檔與參數。
- 輸出包含凍結集 sha256、各策略指標、`delta`（narrowed − baseline）與逐 query 明細（`--json`）。整數累加後才相除、固定 4 位小數、JSON `sort_keys`，同一輸入逐位元可重現。
- 凍結集不合法（含未標註）時 exit 2，錯誤寫到 stderr。

指標定義（N＝query 數；S_q＝該策略對 query q 注入的集合；R_q＝q 的 top-12 候選中標為相關者）：

| 指標 | 定義 |
|---|---|
| Precision@3 | Σ\|S_q ∩ R_q\| ÷ Σ\|S_q\|（micro；所有 query 都沒注入時為 `null`）。 |
| Noise@3 | Σ\|S_q \ R_q\| ÷ N：平均每個 query 注入幾則不相關 note。 |
| Relevant-missed@12 | Σ\|R_q \ S_q\| ÷ Σ\|R_q\|：top-12 內相關 note 沒被注入的比例（無任何相關時為 `null`）。這是「可觀測失敗」的離線代理指標。 |
| 注入字元量 | 每個 query：S_q 為空時 `0`，否則 `block_overhead_chars + Σ chars`；報表給總和與平均。 |

計數欄位（`injected_notes`、`relevant_injected`、`noise_injected`、`relevant_in_top12`、`relevant_missed`、`empty_injections`、`injected_chars`）一併輸出，方便重算與跨樣本合併。

## 4. 門檻校準

門檻由凍結樣本校準，不寫死在程式或文件裡。程序：

1. **產生格點**：`hippo shortlist eval --queries <frozen.json> --max-k <k> --sweep auto`。`auto` 取 `0` 加上樣本 baseline 內出現過的所有 score（`-bm25`）；門檻落在兩個相鄰 score 之間時結果與下一個 score 相同，所以這個格點已涵蓋所有可區分的收窄結果。
2. **選門檻規則**：H1 要求「不增加可觀測的失敗」，因此取 **`extra_missed == 0`（narrowed 的 relevant_missed 不多於 baseline）的最大門檻**，報表以 `recommended_bm25_min_score` 輸出；若每個門檻都會多漏，推薦值為 `null`，代表這個 `max_k` 下無法只靠分數門檻安全收窄。
3. **`max_k` 的選法**：對 `max_k = 3, 2, 1` 各跑一次 sweep，比較推薦門檻下的 Noise@3、注入字元量與 Relevant-missed@12；取在不多漏的前提下字元量最低者。
4. **防過擬合**：推薦值剛好卡在某一則相關 note 的分數上，樣本小時容易過擬合。至少做一次對半切分（依 query id 固定切法）：在 A 半選出的門檻套到 B 半，B 半的 `extra_missed` 仍需為 0；不成立時取兩半推薦值中較小者，或擴大樣本。
5. **依 query 長度分層檢查**：BM25 絕對值隨 query token 數上升。以 `query_terms`（shadow ledger）或 query 長度把樣本分成短／長兩層各跑一次；若兩層推薦門檻差距很大，代表單一絕對門檻不適用，應記錄為 finding 回報 #148，而不是在本票自行發明正規化。
6. **寫入設定並觀察**：把選定的 `bm25_min_score`／`max_k` 寫進 `shortlist.push_shadow`，繼續以 shadow 觀察一段時間，確認「被收窄掉、但同一 session 內被讀取或 applied」的 note（見 1.6）沒有增加，再決定是否開新票實際收窄 production 注入。

## 5. `hippo shortlist freeze`：建立凍結樣本

```bash
hippo shortlist freeze --memory-root <memory_root> --project <slug> \
  --queries-file <queries.txt> --annotator <標註者> --method <標註方法> \
  [--name <樣本名>] [--tool claude-code] [--out <frozen.json>]
```

- 唯讀：不記 offered、不寫 memory root 任何檔案，也不呼叫 LLM。
- 對每行 query 重現 prompt hook 對**全新 session** 的候選路徑：FTS 淨化 → `search()`（該 project、top-12、排除 decayed）→ 與 hook 共用的 `claim_candidates`（依 config 的 `shortlist.collapse_same_topic` 同主題折疊、去除無 slice_id 者）。`candidates` 的前 3 筆因此就是 hook 實際會注入的那幾則（測試以同一 memory root 比對 hook 注入的 id 與字元數），被折疊掉的 note 記在 `collapsed`。
- 輸出 `relevant: null` 的待標註骨架；`chars` 與 `block_overhead_chars` 依 hook 的實際排版與同一個 redaction boundary 計算，並以結構化方式對齊各列（標題含換行、整列被 redaction 替換時仍精確；以固定佔位 session id 計，實際 session id 長度不同時只差常數）。
- 不模擬 session 狀態：session 內去重（已 offer 過的不再 offer）與早停不在凍結樣本內，樣本代表的是「該 prompt 是 session 第一次注入」的情況。
- 會讀 runtime config（`read_hint`、`collapse_same_topic`），請在與 hook 相同的 config 下執行；`eval` 則完全不讀 config。
- 輸出可逐位元重建 hook 的注入字串：`paulsha_hippo.shortlist_eval.render_injection(骨架, query 序號[, note_ids])` 以 `block_header`＋各則 `row`＋`applied_hint` 組回注入內容；測試以它與 hook 實際注入逐位元比對。
- `--annotator`／`--method` 不可空白（exit 2，不產檔），寫入前會去除前後空白。
- `--out` 的父目錄不存在時自動建立（比照 `hippo replay`／`hippo upgrade plan`），既有檔案會被覆寫——已標註的凍結集請另存，不要拿來當 `--out`；寫入失敗時 stderr 回報、exit 1。
- 骨架含真實 query 與 note 標題／路徑：請輸出到私有位置，不要放進任何 repo。

### 5.1 第一版真實樣本的建議做法

- **樣本數**：第一版至少 50 個 query（建議 60–100）。依 tool（claude-code／copilot-cli）與 project 分層，每個 session 最多取 1 個 prompt，避免同一 session 的主題集中。
- **抽樣來源**：從 `<memory_root>/runtime/ledger/offered.jsonl` 以固定亂數種子抽事件（只取「現況確實有注入」的 prompt），用 `session_id`＋`ts` 回到該 host 的 session transcript，取出 `ts` 之前最近的一則使用者 prompt 作為 query。不要用 `hippo recall` 重現，它會新增 offered 紀錄污染 KPI 分母。
- **凍結索引**：抽完 query 後立刻跑一次 `freeze`（或先複製一份 `runtime/indexes/retrieval.db` 到私有位置再以複本作為 memory root 執行），讓候選與分數固定在同一個索引版本。
- **標註方式**：二元標註，標準是「讀了這則 note，是否會實質幫助回應這個 prompt」。標註時隱藏 `bm25` 與排序（例如另存一份只含 `query`／`title`／`path` 的檢視），避免被分數影響；抽 20% 由第二位標註者或隔日第二輪重標，記下一致率，分歧處討論後定案。標註者、標準、盲標與重標方式全部寫進 `annotator`／`method`。
- **保存**：定案的凍結集與 sha256 一起保存在私有位置；之後的評估一律引用同一份檔案，換樣本就換 `name` 與版本。
