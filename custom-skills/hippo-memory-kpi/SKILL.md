---
name: hippo-memory-kpi
description: Use when auditing Hippo session-to-atomic-note conversion, note readability or reference value, Agent viewed or adopted rates, offer/read/applied telemetry, 7-day or 30-day trends, or Cortex memory-consumption semantics.
compatibility: Requires Python 3.10+, a paulsha-hippo checkout or installed package, and read access to the target memory root.
---

# Hippo Memory KPI

## 執行方式

1. 釘住 `memory_root`、UTC `now`、7/30 天窗口，以及目前稽核用的 `tool:session-id`。
2. 解析本 skill 所在目錄，執行 bundled script：

   ```bash
   python <skill-dir>/scripts/report.py \
     --memory-root "$PSC_MEMORY_ROOT" \
     --days 7 --days 30 \
     --exclude-session codex:<current-session-id> \
     --format json
   ```

3. 用同一組參數改成 `--format markdown` 取得可直接交付的表格。
4. 若使用者同時問「目前執行版本」，另以唯讀方式核對 `hippo --version`、systemd `ExecStart` 與 build identity；不要把 repo HEAD 當成 deployed runtime。

稽核期間不要執行 `hippo recall`。Recall 會新增 offer，使觀測行為本身污染分母；需要排除的目前 session 改用 `--exclude-session`，可重複指定。

## 指標契約

| 指標 | 主分子／分母 | 證據來源 |
|---|---|---|
| Session → Atomic note | final `processing.state=promoted` / non-empty unique intake sessions | `import.jsonl` + folded `processing.jsonl` |
| 機器可讀 proxy | current file 且 schema/checksum valid / committed produced unique notes | `publication.jsonl` + knowledge files |
| 參考價值 proxy | searchable / produced unique notes | retrieval index |
| 調整後參考價值 | searchable / machine-valid non-review notes | knowledge files + retrieval index |
| 原始 Read（補充） | 排除後所有 unique `source=read` notes | `memory_usage.jsonl` |
| Agent 看過率 | unique offered notes with a strictly later matching read / eligible unique offered notes | `offered.jsonl` + `memory_usage.jsonl` |
| Agent 嚴格採用率 | unique offered notes with `offer < read < applied` / eligible unique offered notes | both usage ledgers |

主轉化率保留 `no-findings`、`parked`、`pending` 的狀態列；`empty-skip` 不進 non-empty 分母。`substantive_conversion` 只作補充，分母為 `promoted + parked`。

主消費漏斗以 unique atomic notes 計算，並排除 final `state=no-findings` 與目前稽核 session。原始 Read 包含 pre-offer／never-offered；`direct_read_events` 則專指無法歸因至先行 offer 的 Read。寬鬆 applied 仍須 `offer < applied`，只作補充。

## Cortex 分類

| 行為 | 分類 | 是否進主漏斗 |
|---|---|---|
| Cortex `source_material` 直接放進 job prompt | neither | 否 |
| Hippo shortlist/context 注入並寫 `offered.jsonl` | offer | 分母 |
| Claude `Read`／Copilot `view` 實際開 note 並寫 `source=read` | read | 時序與 identity 成立才進看過分子 |
| `hippo usage mark-applied` | applied | 先有 attributable read 才進嚴格採用分子 |

Cortex 中 Agent 真正用 `Read`/`view` 開啟 note 算 **read**；只是 prompt 已含摘要不算。Codex 缺少與 Claude/Copilot 對等且已 live-proven 的 read hook，因此看過率是可觀測下限，不可把零值解讀為確定沒看。

## 報表形狀

先交付 script 產生的主表，再附三項限制：

- 「可讀性」是 schema/checksum 的機器 proxy，不是人類理解或內容正確性。
- `searchable` 代表可檢索，不代表曾被讀取、引用或採用。
- 零分母、缺 index 或 index integrity problem 顯示 `n/a`；ledger 缺檔／壞行列 diagnostics。診斷非零時數值只代表可解析子集，不要重建或修復。

## Quick reference

| 需求 | 參數／判定 |
|---|---|
| 可重現快照 | `--now <ISO-8601>` |
| 排除 observer effect | `--exclude-session tool:id` |
| Agent 看過 | same tool/session/slice 且 `read_ts > offer_ts` |
| 嚴格採用 | same triple 且 `offer_ts < read_ts < applied_ts` |
| 輸出 | `--format json` 或 `markdown` |

## 常見錯誤

| 錯誤 | 修正 |
|---|---|
| 用現存有效檔案反推 session 是否轉化 | session 轉化只看 final `promoted`；檔案有效率另算 |
| 把 applied-before-offer 或 applied-only 當採用 | 主指標只計 ordered `offer → read → applied` |
| 把 Cortex prompt injection 當 read | 無 Hippo read event 就是 neither |
| 稽核時先 recall 再量測 | 不呼叫 recall，排除目前 session |
