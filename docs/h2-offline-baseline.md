# H2 離線 benchmark：凍結的 as-of BM25 基準線

> 對應 issue #164（#148 H2 C 組的前置）。H2 比較三種 pull 篩選：
> - **A**：BM25 top-3；
> - **B**：BM25 top-12 再由 LLM 篩成 0–3 則；
> - **C**：BM25 top-12 再由 JEV 對每則問 yes／no，篩成 0–3 則。
>
> 三組吃同一份 BM25 top-12 候選。本文件定義這份候選怎麼產生、怎麼凍結、有哪些限制。
> 規格來源是 #148 的 2026-09-27 登錄留言。

## 1. 工具

```bash
hippo h2 freeze --memory-root <memory_root> --tasks <tasks.json> --out-dir <私有目錄> \
  --allow-repo github.com/hamanpaul/paulsha-cortex --allow-repo github.com/hamanpaul/paulsha-hippo … \
  [--deny-terms <私有字詞清單>] [--snapshot <既有快照>]
```

- 唯讀：只複製 `<memory_root>/runtime/indexes/retrieval.db` 成快照，不改 memory root，也不呼叫任何 LLM 或外部服務。
- 輸出：`--out-dir` 底下的 `index-snapshot.db` 與 `frozen-candidates.json`；stdout 是覆蓋統計。
- `--snapshot` 沿用既有快照重跑，同一份快照＋task 檔＋私有字詞清單會得到相同的 `digest`。

## 2. 檢索規則

1. **query：** task 的「標題＋內文摘錄」經 `retrieval.to_fts_query()` 淨化，和 prompt hook、task-memory 用同一個 sanitizer。
2. **範圍：** 只在 task 所屬 project（`github.com/<owner>/<repo>`）的索引列中檢索。
3. **as-of：** 只保留 `captured_at` 早於 task 的 `as_of`（issue 建立時間）的 slice。
   - `captured_at` 解析不出來或沒有時區，視為時間不可信，排除（`time-untrusted`）。
4. **自身 issue：** 標題或內文提到 task 自己 issue 號碼（`#<n>`）的 slice 排除（`mentions-task-issue`），作為時間過濾之外的額外洩漏防線。
5. **排序：** 純 FTS5 `bm25()`，越負越相關；同分以 `slice_id` 排。取 top-12，A 組＝前 3。

### 為什麼不用正式排序

正式的 `search()` 除了 bm25，還會用 `link_weight`、已讀次數加權（usage boost）與 `active` 旗標。這些都是「現在」的狀態：拿來排 as-of 的題目，會把 task 之後才發生的閱讀或連結帶進來。

因此基準線：
- 只用純 bm25；
- as-of 之前的 slice，不論現在是否 decayed 都納入。

三組共用同一份候選，組間比較仍然公平。但 A 組的數字**不等於**正式 pull 的線上表現，報告時要註明。

## 3. 送出前掃描（C 組）

C 組會把候選送給外部 processor，所以每則候選與 task 文字本身都要先掃。

- **候選送出的內容：**
  - 只有標題＋內文前 800 字元（`body_view`），不含 frontmatter；
  - B 組看同一份內容，兩組條件一致。
- **內建規則**（repo 內，只含通用規則）：
  - 本機絕對路徑（`/home/…`、`/Users/…`、`/root/…`、`/mnt/<x>/…`、Windows 磁碟路徑）；
  - email（`git@github.com` 這類 remote 位址除外）；
  - private IPv4；
  - 疑似 secret（雲端 key、GitHub token、private key 標頭等）。
- **私有字詞清單：**
  - 由 `--deny-terms` 指定的私有檔案提供，例如 private repo 名稱、雇主或客戶字詞，**不進本 repo**；
  - 輸出只記清單的 sha256，不記內容。
- **命中就整則標為不可送出**（`egress: excluded`，`egress_reasons` 記規則名），不做局部遮蔽。
- **task 所屬 repo 不在 `--allow-repo` 清單內**，task 標為不可送出（`repo-not-allowlisted`）。

## 4. 格式

### task 檔（`format: hippo-h2-tasks`，`version: 1`）

| 欄位 | 說明 |
|---|---|
| `task_id` | 唯一字串。 |
| `repo` | `github.com/<owner>/<repo>`。 |
| `issue` | issue 號碼（正整數）。 |
| `title`／`body` | issue 標題與可公開的內文摘錄。 |
| `as_of` | 含時區的 ISO8601，issue 建立時間。 |
| `split` | `dev` 或 `hidden-pool`。 |

### 凍結候選集（`format: hippo-h2-frozen-candidates`，`version: 1`）

- **頂層：**
  - `params`：fetch_k、a_k、body_view_chars、排序方式、as-of 欄位、public 允許清單、私有字詞清單的 sha256；
  - `index_snapshot_sha256`、`tasks`、`digest`（不含 `frozen_at` 的整份 canonical JSON sha256）、`frozen_at`。
- **每個 task：**
  - task 欄位、`fts_query`；
  - `task_egress`／`task_egress_reasons`；
  - `matched`（排除前的 FTS 命中數）、`excluded_before_rank`（各排除原因的數量）；
  - `candidates`、`arm_a`。
- **每個候選：** `rank`、`slice_id`、`bm25`、`captured_at`、`title`、`body_view`、`egress`、`egress_reasons`。

## 5. 私有資料的放置

凍結候選集含記憶內文，**只能放在私有位置**，例如 `~/.local/share/paulsha-hippo/experiments/h2-offline/`，不得提交進本 repo（比照 `docs/push-shadow-baseline.md` 對真實樣本的規則）。

本 repo 只放工具、文件與合成測試資料（`tests/test_h2_offline.py`）。

## 6. task 抽樣與 hidden 組成規則（凍結前登錄）

**task 抽樣：**
- **範圍：** public 允許清單內各 repo 的**已關閉 issue**（不含 PR）。
- **時間：** issue 建立時間要晚於該 repo bucket 最早的 `captured_at` 至少 3 天，確保 as-of 之前有記憶可檢索。
- **排除：**
  - 標題以 `chore(release)`、`release` 開頭的版本 issue；
  - issue 文字本身未通過第 3 節掃描的（task 必須能送 C 組）。
- **內文：** 只取前 1,500 字元。
- **抽樣方式：** 每個 repo 依 `sha256("<seed>|<repo>|<issue>")` 排序後取配額題數。
- **配額（共 44 題）：**
  - serialwrap 2、paulsha-conventions 6、paulsha-patchmud 6、paulsha-hippo 10，其餘由 paulsha-cortex 補滿；
  - 某 repo 可用題數不足配額時，差額由 paulsha-cortex 補。
- **分組：** 全部入選題依另一個 hash `sha256("<seed>|split|<repo>|<issue>")` 排序，前 12 題為 dev，其餘 32 題為 hidden 候選池。
  不沿用入選時的 hash：大 repo 入選的題目 hash 都集中在小值端，沿用會讓 dev 全落在同一個 repo。
- **可重現：** seed 固定為 `h2-20260927`。抽樣腳本是 `scripts/h2_sample_tasks.py`，會把 seed、配額、各 repo 可用與入選題數寫進 task 檔頂層的 `sampling` 欄位。

**hidden 最終 24 題**（標註完成後、任何一組執行前套用；只看標註，不看任何一組的輸出）：
- 從候選池依 seed 順序，先取所有「top-12 沒有任何 relevant」的題目（最多 8 題）；
- 再依 seed 順序補滿 24 題；
- 零 relevant 的題目不足 6 題時，照實回報為限制，不另外補題。

## 7. A／B／C benchmark（#167）

```bash
hippo h2 run --frozen <frozen-candidates.json> --split-file <split.json> --split dev|hidden \
  --arms A,B,C --out <records.jsonl> --deny-terms <私有字詞清單> [--stability]
hippo h2 score --frozen <…> --gold <gold.json> --split-file <…> --split hidden --records <records.jsonl>
```

**三組：**

| 組 | 看到的候選 | 選法 |
|---|---|---|
| A | 全部凍結候選 | BM25 前 3（線上現況，不經送出前過濾） |
| B | 可送出的候選 | Claude（`sonnet`）一次看完，回 0–3 則 |
| C | 可送出的候選 | JEV（`jev-1.13.0`）每則一題 yes／no，yes 者依 BM25 名次取前 3，全 no 回 0 則 |

- **B、C 看同一份候選**，品質比較才公平；送出前過濾的損失由隱私門檻另外量。
- **C 送出前會用私有字詞清單再掃一次實際 payload**，命中就不送，並記為 `blocked`。這是「TypeSafe 政策違規＝0」的驗證依據。

**計分**（`hippo h2 score`，確定性）：
- **Precision@3：** 所有題目選中的候選中，相關者的比例。
- **每題不相關數：** 平均每題選中幾則不相關的候選。
- **task 命中率：** top-12 有相關記憶的題目中，至少選中一則相關的比例。
- **正確回 0 則的比例：** top-12 沒有相關記憶的題目中，選 0 則的比例。
- **另報：** 注入字元量、median 延遲、每題成本（`Decimal`）、可送出涵蓋率、被過濾掉的相關候選比例。
- **穩定性：** hidden 依 seed 抽 8 題，B、C 各重跑一次，只報告、不列入門檻。

**go 條件**（`h2_bench.GO_THRESHOLDS`，以 hidden 24 為準，全部通過才算 go）：
- **C 對 A：**
  - task 命中率不低於 A 超過 5pp；
  - Precision@3 高 ≥ 10pp，或每題不相關數少 ≥ 30%；
  - 正確回 0 則 ≥ 90%。
- **C 對 B：**
  - Precision@3 差距 ≤ 5pp、task 命中率差距 ≤ 10pp；
  - median 延遲快 ≥ 70%，或成本低 ≥ 90%。
- **隱私：**
  - 政策違規（`blocked`）＝0；
  - 可送出涵蓋率 ≥ 80%；
  - 被過濾掉的相關候選 ≤ 5%。

**gold 標註**（私有；流程依 2026-09-27 決策紀錄 v4）：
- Codex `gpt-6-sol` 與 Claude（`opus`）各自盲標。
- 明確分歧交 Codex `gpt-5.6-terra` 仲裁；v4 原寫 `gpt-6-terra`，但在該帳號不存在。
- 有人標「不確定」的項目：三方多數決，不確定視為棄權；平手以仲裁者為準。
- Paul 抽查 5% 一致項。
- gold 與 hidden 名單的 sha256 在 hidden 執行前登錄於 #167。

## 8. 第一輪結果（2026-09-27）：no-go for now

hidden 24 題、依第 7 節門檻判定，**no-go**。詳細數據與檔案 sha256 登錄於 #167。

| 組 | Precision@3 | 每題不相關數 | task 命中率 | 正確回 0 則 | median 延遲 | 每題成本 |
|---|---|---|---|---|---|---|
| A BM25 | 0.333 | 2.00 | 12/17 | 0/7 | — | 0 |
| B Claude | 0.938 | 0.08 | 14/17 | 7/7 | 11.4 s | US$0.031 |
| C JEV rev1 | 0.818 | 0.25 | 13/17 | 6/7 | 0.33 s | US$0.00022 |

- **通過：**
  - C 對 A 的命中率與品質：Precision@3 高 48pp，不相關數少 88%；
  - C 對 B 的命中率（差 5.9pp）與效率：延遲快約 97%，成本低約 99%；
  - 隱私：違規 0、涵蓋率 92%、被過濾掉的相關候選 2.9%。
- **未過：**
  - 正確回 0 則：6/7（86%），門檻 90%；
  - C 對 B 的 Precision@3 差距 11.9pp，門檻 5pp。
- **兩項都來自同一題：** 一個沒有相關記憶的 closeout task，C 選了同一功能的 3 則先前執行紀錄。這只是描述，不改判定；hidden 執行後不修改標註、criteria 或門檻。
- **門檻設計的限制：** hidden 只有 7 題沒有相關記憶，「正確回 0 則 ≥ 90%」實際上等於 7 題全對。
- **穩定性：** 抽 8 題重跑，B、C 各 6/8 選擇完全一致。

依決策紀錄 v4：離線 go 之前不開 live shadow 或 randomized H2，也不改 #857 的 production 路徑。

## 9. 第二輪（v4.1，#169）規則

> 依 2026-09-27 決策紀錄 v4.1。v4 的判定維持 no-go；v4.1 是新的假設：C 值不值得取代 A，成為 public-memory pull 的選用相關性篩選。只有這一輪；v4.1 任何門檻未過，Hippo × JEV 相關性篩選就結案。

### 題目與 gold

```bash
python3 scripts/h2_sample_tasks.py --memory-root <…> --deny-terms <…> --seed <登錄的 seed> \
  --total 60 --dev 0 --exclude-tasks <第一輪 tasks.json> --quota github.com/hamanpaul/paulsha-hippo=100 --out <batch1.json>
hippo h2 split --frozen <frozen-candidates.json> --gold <gold.json> --seed <登錄的 seed> --out <split.json>
```

- **題目：** 全部使用第一輪沒用過的題目（`--exclude-tasks`）。同一個 seed 的排序是確定的；追加一批時，把前面各批也列入 `--exclude-tasks`，再取下一段。
- **gold 規則**（事先固定）：
  - 兩位主標註者一致且不是 uncertain → gold；
  - 其餘交仲裁，三方做 non-U 多數決，uncertain 視為棄權；
  - 仍然沒有明確多數 → `unresolved`，不納入 gold；
  - Paul 只做 5% 抽查。
- **分層：**
  - empty：top-12 全部不相關；
  - non-empty：至少一則相關；
  - indeterminate：其餘，例如含 `unresolved`，不抽進 dev／hidden；
  - C 送不出去的題目（task 不可送出，或沒有可送出的候選）也不抽：C 在這種題目一定回 0 則，放進 empty 層等於白拿一題。
- **`hippo h2 split`：**
  - 依 `sha256(seed|task_id)` 排序，先抽 hidden：empty 20、non-empty 20，同層內非 cortex 的題目優先；
  - 再從剩下的題目抽 dev 12 題，其中 empty 3 題；
  - 題數不足就報錯，依決策紀錄判 no-go for now；
  - hidden 非 cortex 少於 6 題時，輸出 `generalization_scope: cortex-dominant-public-engineering`。

### 執行與計分

```bash
hippo h2 run --protocol v4.1 --frozen <…> --split-file <split.json> --split dev|hidden --arms A,B,C --out <…> --deny-terms <…> [--stability] [--c-revision rev3]
hippo h2 score --protocol v4.1 --frozen <…> --gold <…> --split-file <split.json> --split hidden --records <…>
```

- **C 問法：**
  - 起始版是 `rev2`：「同 repo／同元件／同功能」本身不足以判 yes，單純的歷史執行紀錄也不算；
  - 在 dev 上最多修一次，改成 `rev3`，之後凍結。
  - `rev3`（dev 修訂，#169）：rev2 在 dev 的命中率 5/9 低於 A 的 6/9，漏掉的多是本題要改的程式碼或流程的規格、契約、測試預期。rev3 把這類記憶明寫進 yes；「同 repo／同元件／同功能」改成「沒有規則或結果時才不足」；「歷史執行紀錄不足」維持不變。dev 結果：命中率仍是 5/9，empty 正確回 0 則由 3/3 降為 2/3，**不採用**；hidden 沿用 `rev2`（dev 上二選一，未看 hidden）。
- **穩定性：** hidden 依 `h2-v41-20260927` 抽 10 題重跑。
- **P@3 的分母**只算有 gold 的選擇；選到 `unresolved` 另外計數。v4 的 gold 沒有 `unresolved`，第一輪的分數不變。

**go 條件**（`h2_bench.GO_THRESHOLDS_V41`；產品效用要求，不是依 v4 觀察值推估；全部通過才算 go）：

| 類別 | 門檻 |
|---|---|
| 隱私 | 政策違規＝0；可送出涵蓋率 ≥ 80%；被過濾掉的相關候選 ≤ 5% |
| empty 層 | C 正確回 0 則 ≥ 90%（18/20） |
| non-empty 層 | C 命中率 ≥ A − 5pp；Precision@3 ≥ 0.80，且 ≥ A ＋ 20pp；每題不相關數 ≤ A 的 50% |
| 運作（hidden 全體） | C 的 median 延遲 ≤ 1 s；每題成本 ≤ US$0.001 |
| 穩定性 | C 品質穩定 ≥ 9 題 |

- **品質穩定的定義：**
  - non-empty 題：重跑的不相關數不增加；原本有命中的，重跑後不得掉成 0 則相關；
  - empty 題：「回 0 則／有選」的判定不翻轉。
- **只報告，不列入門檻：** B 的全部指標，以及選擇完全一致的比例。
- **hidden 若含 indeterminate 題目**，直接判 no-go。

## 10. 第二輪（v4.1）結果（2026-09-28）：no-go，Hippo × JEV 相關性篩選結案

hidden 40 題依第 9 節門檻判定，**no-go**。依決策紀錄 v4.1，**Hippo × JEV 相關性篩選就此結案**：不開 live shadow，也不改 #857 的 production 路徑。詳細數據與檔案 sha256 登錄於 #169。

| 組 | non-empty P@3 | 每題不相關數 | 命中率 | empty 正確回 0 則 | median 延遲 | 每題成本 |
|---|---|---|---|---|---|---|
| A BM25 | 0.293 | 2.05 | 11/20 | 0/20 | — | 0 |
| B Claude（只報告） | 0.639 | 0.65 | 12/20 | 14/20 | 16.2 s | US$0.034 |
| C JEV rev2 | 0.607 | 0.55 | 12/20 | 16/20 | 0.33 s | US$0.00021 |

- **未過：**
  - Precision@3 0.607，門檻 0.80；
  - empty 層正確回 0 則 16/20，門檻 18/20；
  - 被隱私過濾掉的相關候選 12.5%，門檻 5%。
- **通過：**
  - 命中率不低於 A（12/20 對 11/20）；
  - Precision@3 比 A 高 31pp；
  - 不相關數比 A 少 73%；
  - 延遲與成本；
  - 品質穩定 9/10；
  - 政策違規 0、可送出涵蓋率 94.6%。
- **錯誤分布：** 沒有集中在單一題。
  - C 在 empty 層有 4 題選了記憶；
  - non-empty 層的 11 則不相關選擇分散在 7 題，其中 10 則在 hippo 的題目。
- **隱私限制是結構性的：** 被過濾掉的 7 則相關候選全部來自 hippo 的題目，原因都是私有字詞。hippo 自己的記憶常提到私有專案或雇主相關詞，只能送 public 資料的外部篩選，在這類題目上天生會漏。
- **兩輪合併的結論：**
  - C 穩定地比 BM25 精準、便宜，也夠快；
  - 但精準度沒有達到產品門檻，「沒有相關記憶時回 0 則」也不夠可靠；
  - B（Claude 篩選）在這一輪同樣沒有達到 0.80 的精準度，表示題目本身難度偏高，但這不改變 C 的判定。

## 11. v5：連續分數重排序（#173）

> 依 2026-09-28 決策紀錄 v5（Paul 採納）。v4／v4.1 的 Choice 篩選與「JEV 決定回 0 則」維持 no-go。v5 是另外立案的新假設：JEV 的連續分數能不能把 BM25 top-12 排得更好，並固定回 top-3。

### 正式候選：R75-slot-v1（`h2_bench.rerank_r75`）

1. **打分數：** BM25 top-12 中，只對可送出的候選各送一個單題 Noul request（`jev-1.13.0`），保存 P(true)。
2. **排出 pseudo-rank：** 可送出的候選依 P(true) 由高到低排序，排第 j 的取得 pseudo-rank＝第 j 個可送出候選原本的 BM25 名次。
3. **融合：** `fused = 0.75·score(pseudo-rank) + 0.25·score(BM25 名次)`，其中 `score(r)=(12−r)/11`。
4. **重排：** 只在可送出候選原本佔的位置之間依 fused 重排。**不可送出的候選位置完全不動**；同分時 BM25 名次優先。
5. **固定回 top-3，不做回 0 則。**
6. **執行：** 12 路並行；每題只呼叫一次、不重試，逾時 5 s。
7. **整題回退 A（BM25 top-3）：** task 不可送出、可送出的候選 ≤ 1 則、送出前掃描命中，或任何一題錯誤／逾時。

### L 對照組（`h2_bench.CodexRanker`）

- **怎麼跑：** Codex `gpt-6-luna`（reasoning effort max）看全部 12 則候選，依與 R75 相同的判準由最相關排到最不相關，取前 3。
- **執行環境：** `codex exec`，read-only sandbox、ephemeral，關閉 plugins／memories／goals／hooks／shell_tool，stdin 導 /dev/null。
- **角色：** 只作對照報告，不影響判定。

### Q0 流程

```bash
python3 scripts/h2_sample_tasks.py … --seed h2-v5-20260928 --total 90 --dev 0 --exclude-tasks <先前各輪 tasks.json …> \
  --quota github.com/hamanpaul/paulsha-hippo=0 --quota github.com/hamanpaul/paulsha-patchmud=0 \
  --quota github.com/hamanpaul/paulsha-conventions=0 --quota github.com/hamanpaul/serialwrap=0 --out <batch.json>
hippo h2 split --protocol v5 --n 60 --frozen <…> --gold <…> --out <split.json>
hippo h2 run --protocol v5 --split qualification --arms A,R,L --frozen <…> --split-file <split.json> --out <…> --deny-terms <…>
hippo h2 score --protocol v5 --split qualification --frozen <…> --gold <…> --split-file <split.json> --records <…>
```

- **題目：** 依凍結集的題目順序，取前 60 題 non-empty。non-empty 以完整 top-12 gold 判定，包含不可送出的候選。最多掃 130 題，不足 60 題就判 underpowered。
- **gold：** 主標註者為 Codex `gpt-6-sol`、`gpt-6-astra`，仲裁 `gpt-5.6-terra`，不使用 Claude；其餘規則同第 9 節。
- **門檻**（`GO_THRESHOLDS_V5`；配對 task bootstrap，10,000 次，seed `h2-v5-q0-bootstrap`）：
  - ΔNDCG@3（R75−A）點估計 ≥ ＋0.10，且 95% CI 下界 > 0；
  - Recall@3：CI 下界 ≥ −0.05；
  - P@3：點估計 ≥ 0，且 CI 下界 ≥ −0.05。
- **延遲：** R75 送出的題目，pull 新增 wall 的 p90 ≤ 4 s 才能進 Q1；品質通過但延遲未過時，判定為 `quality-qualified/runtime-not-qualified`。
- **必報：** 掃描題目的 empty 比例、每次 pull 的相關 slot 數、L 對 A 與 R75 對 L 的差值、L 的延遲。
- **範圍：** Q0 只證明排序更符合凍結的相關性標準（offline public paulsha-cortex、以 BM25 top-12 為前提），不代表產品有價值。產品價值要到 Q2 的 live 隨機對照才能判定。
