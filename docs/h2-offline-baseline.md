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
