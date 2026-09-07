---
status: accepted
work_item: issue-146-task-memory-payload
---

# 通用 task memory payload 與 capability-aware 內容交付設計

- 日期：2026-09-07
- Issue：[#146](https://github.com/hamanpaul/paulsha-hippo/issues/146)
- 狀態：已核可為正式規劃基線，待分階段實作與驗證

## 設計邊界

Hippo 負責通用 payload、候選檢索、交付 mode 與 evidence contract；host 負責把自身 capability/permission result 映射到
契約。Cortex [#857](https://github.com/hamanpaul/paulsha-cortex/issues/857) 是首個 adapter，不是核心的特殊分支。兩個
repo 的 work item 分開，Cortex adapter 依賴 Hippo schema 與 provider capability result。

## Decisions

### D1：以 task envelope 作為唯一交付邊界

選擇版本化 envelope，而非讓每個 host 直接讀 Hippo 內部檔案或 ledger。envelope 能攜帶候選、能力結果與 evidence，並可在
未來增加欄位而保持舊 consumer 可讀；不把 raw note、secret 或個人路徑暴露到 shareable artifact。runtime evidence 如需
task/session attribution，僅保留 bounded ID；公開 spec/fixture/report 使用合成 ID 與去識別路徑。

### D2：intent 檢索先產生 bounded candidates

候選階段只接受 task intent 與既有可見性邊界，最多回傳三筆穩定排序候選。無候選、無權限與索引/provider 不可用是不同
狀態，不能以空陣列掩蓋原因，也不能以 host/project 名稱改變排序或授權。

### D3：能力協商決定 mode

mode 選擇由 payload provider 回報的 capability facts 與 host 允許範圍共同決定：inline/context-delivered 代表內容已交付但
不是 Read；snapshot/materialized 只代表唯讀 manifest 已 ready/offer，只有實際 tool/provider 成功返回內容才是
`content-returned`。需要 note fetch 時必須產生 read attempt 並回報 return/failure；能力不足或 provider 缺失則輸出
ineligible/failure，檔案已寫好不能取代內容返回證據。
禁止「嘗試 Read 失敗後假裝 inline 成功」的無證據降級。

### D4：evidence 是工具中立事件

Hippo 定義事件類型與必要識別欄位，host 可附自己的 non-secret metadata。事件順序必須能對帳 attempt → returned/failed →
applied；缺少任一環節不自動推導成功。這使不同 tool、host 與 executor 可共用 strict KPI 對帳，而不綁定某一模型名稱。

### D5：KPI 層與交付層分離

保留現行 offered/read/read-through 與 retention 語意。payload 交付、inline、snapshot 只做交付觀測；只有既有 Read 定義
要求的事件才進 read 分子。新 evidence 不改既有 strict KPI 分母，另以 path-level report 支援首道 95% gate。

### D6：權限與失敗 fail closed

此設計不新增全域權限、不關閉 sandbox、不繞過 daemon/provider gate、不直接寫 raw durable state。Hippo 未安裝或 provider
不可用時保留既有 dispatch 結果與 failure evidence；adapter 不得自己發明 fallback 授權。

## Data flow

```text
task intent
   -> Hippo candidate retrieval (<=3, authorized only)
   -> capability negotiation
   -> inline | snapshot | note_fetch | ineligible | failure
   -> tool-neutral attempt/return/failure/applied evidence
   -> strict KPI unchanged + path-level canary report
```

## Compatibility and rollout

先落地 schema/validation 與 provider capability contract，再由 Cortex adapter 做每 path 至少 5 筆的 canary。只有 eligible
authorized retrieval >=95% 且 evidence 完整時，才進 utility trial；utility 失敗不回頭放寬授權或變更 KPI。舊 consumer 對
未知 optional 欄位保持可讀，未知 mode 必須視為 ineligible/failure 而非成功。

## Review gates

1. schema、candidate bound、mode 與 evidence 的 RED/GREEN 測試。
2. Hippo 全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict`。
3. Cortex adapter 透過正式 `cortex work intake` 取得可持久化 work item/run；若 provider、project registration 或 hard gate
   阻擋，保存 CLI 的 fail-closed evidence，不以手動 state 或 infra 修改繞過。
