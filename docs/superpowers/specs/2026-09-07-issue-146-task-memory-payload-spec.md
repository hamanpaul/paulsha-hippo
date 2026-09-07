---
status: accepted
work_item: issue-146-task-memory-payload
---

# 通用 task memory payload 與 capability-aware 內容交付規格（issue #146）

- Issue：[#146](https://github.com/hamanpaul/paulsha-hippo/issues/146)
- 依賴的首個 host adapter：Cortex [#857](https://github.com/hamanpaul/paulsha-cortex/issues/857)
- 本文件只定義 Hippo 通用核心契約；不把任何 host、project、executor 或模型名稱寫入契約。

## Problem and outcome

目前跨 session 的 task memory 需要同時回答「這項任務需要什麼」與「目前執行環境能安全取得什麼」。現有聚合觀察涵蓋
278 個 Cortex/Copilot offer session，其中 222 個有 log、126 個曾嘗試取閱 offered note；Hippo 另有 299 次 memory
view 全部 permission denied。這些數字是跨 session 聚合證據，不足以證明每個 job 所在的 permission layer，也不應被簡化成
單一旗標即可修復的問題；另有缺少 log 的 session 不作成功或失敗推論。

預期結果是將 task memory 內容、能力協商、取得嘗試與結果分離成可驗證的通用 payload：符合能力且有授權時可靠交付；能力
不足、provider 不可用或取得失敗時明確回報，不把 inline 注入冒充 Read；既有嚴格 KPI 的分母與語意維持不變。

## Requirements

### R1. 通用 task envelope

Hippo SHALL 提供版本化、可 redaction 的 task memory payload，至少包含：

- `task_id` 與 bounded `intent`（任務意圖，不複製整段 prompt）；
- `candidates`（最多 3 個、可為空；每項有穩定 reference、摘要、授權/可用性結果）；
- `delivery`（協商後的交付 mode 與 capability facts）；
- `evidence`（取得嘗試、返回、失敗與 downstream applied 結果）；
- `schema_version` 與 producer/adapter identity；公開 spec/fixture/report 不包含 secret、私密筆記正文或個人絕對路徑。runtime evidence 可保留必要且 bounded 的 task/session attribution ID，但公開 fixture 一律使用合成 ID、去識別路徑。

### R2. 依 task intent 檢索且有界

候選檢索 SHALL 以 task intent 與既有索引/權限邊界為輸入，固定最多 3 筆並保留 deterministic ordering。不得以 host
project 名稱作 special case，也不得把未授權內容混入候選或透過 fallback 取得。

### R3. Capability-aware delivery

交付 mode SHALL 明確區分：`inline`、`snapshot`、`note_fetch`、`ineligible`、`failure`。inline/context-delivered 只是
payload 交付，**不計為 read**；snapshot/materialized 只代表唯讀 manifest/content artifact 已 ready/offer，檔案存在本身
不是 content returned。只有實際 tool/provider 成功返回內容才能產生 `content-returned`；需要另行取閱時，adapter 必須
先聲明對應 capability，並將不可用、拒絕、逾時等結果回報為可分類的 failure/ineligible，不得默默降級成成功。

### R4. Tool-neutral evidence

核心 SHALL 定義與工具無關的事件與欄位，至少能表達 `read_attempt`、`returned`、`failed`、`applied` 與失敗分類；
host adapter 可將自己的工具事件映射進來，但不可要求某個工具、模型或 executor identity 才算有效。

### R5. KPI compatibility

既有 strict KPI 與 retention/usage 語意 SHALL 維持。inline、snapshot 與 note fetch 各自可觀測，但只有實際符合既有
read 定義的行為才進 read/read-through 分子；不可藉由改分母或把 inline 當 read 達標。

### R6. 安全與失敗邊界

本項不得擴大全域 allow-all、關閉 sandbox、繞過 permission gate、直接改 ledger/registry 或以 recall 取代授權檢查。
Hippo 未安裝或 payload provider 不可用時，既有 dispatch 行為與拒絕/失敗證據須保留；任何新 mode 都必須 fail closed。

### R7. 首個 host adapter 驗證

Cortex 是首個 host adapter 與驗證場景，但僅依賴本通用契約，不引入 Cortex/專案名稱特判。Cortex 端工作見
[#857](https://github.com/hamanpaul/paulsha-cortex/issues/857)，其依賴本 issue 的契約與 provider capability 回報。

### R8. 分階段驗收

第一道 gate 是 eligible、已授權內容的有效取得率至少 95%；canary 應按 delivery/capability path 分組且每組至少 5
筆，逐筆保存 attempt/return/failure/applied evidence。第一道 gate 通過後才進真正 utility trial；utility 不能回頭改寫
strict KPI 或把未授權成功算入分母。

## Non-goals

- 不為任何 host/project 建立 special case 或共享預設模型要求。
- 不把 inline injection、候選顯示或 snapshot 產生誤報為 Read。
- 不在本 issue 內實作 Cortex lifecycle、project registration、provider infrastructure 或 GitHub dispatch。
- 不弱化既有權限、sandbox、ledger/registry ownership 或 fail-closed gate。

## Acceptance

- payload schema、mode、capability 與 tool-neutral evidence 有 round-trip/validation 測試；公開 fixture/report 使用合成 attribution ID、去識別路徑，且不含秘密或私密筆記正文。
- 依 task intent 的候選上限、排序、授權過濾與 provider unavailable/failure 分類有測試。
- inline 不會新增 read；真正 read、失敗與 applied 的 KPI 對帳與現行 strict KPI 相容。
- Cortex adapter 能使用本契約完成分 path canary，並以 >=95% eligible authorized retrieval 作第一道 gate。
- Hippo 的 pytest、policy_check、OpenSpec strict 與文件治理檢查全綠；本 PR 僅為正式規劃/契約，不宣稱 runtime 已完成。
