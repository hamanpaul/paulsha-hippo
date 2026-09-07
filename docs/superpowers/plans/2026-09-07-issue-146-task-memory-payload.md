---
status: accepted
work_item: issue-146-task-memory-payload
---

# issue-146-task-memory-payload Implementation Plan

> 本 PR 只持久化可審查的正式契約與工作分解，不宣稱 Hippo 或 Cortex runtime 已完成。

## Scope and dependencies

- Core repo：`hamanpaul/paulsha-hippo`，issue [#146](https://github.com/hamanpaul/paulsha-hippo/issues/146)。
- Host adapter：`hamanpaul/paulsha-cortex`，issue [#857](https://github.com/hamanpaul/paulsha-cortex/issues/857)。
- Cortex adapter 依賴本計劃的 payload schema/capability/evidence contract；兩 repo 不共用 special case，也不固定下游模型或 executor。
- 首道 gate：每個 delivery/capability path 至少 5 筆 canary，eligible authorized retrieval >=95%；通過後才做 utility trial。

## Global constraints

- PR、commit、changelog 與文件使用 zh-tw；公開 spec/fixture/report 使用合成 task/session attribution ID 與去識別路徑，不含 secrets 或私密筆記正文。runtime evidence 可保留必要且 bounded 的 attribution ID。
- inline 不算 read；strict KPI 的分母、read/read-through 與 retention 語意不變。
- 不擴大全域權限、不關閉 sandbox、不改 raw ledger/registry、不用 recall 繞過授權。
- 不以 Hippo/Cortex project name special case，亦不要求 Luna 或任何固定模型 identity；下游沿現行 routing。
- 先做測試契約，再做最小核心實作；每步保存工具中立 attempt/return/failure/applied evidence。

## Task 1：固定 payload 與 candidate contract（RED）

- [ ] 為 envelope、schema version、intent、最多三筆 candidates、授權/可用性結果寫 validation tests。
- [ ] 為 deterministic ordering、空候選、provider unavailable、未授權候選與 redaction 寫失敗測試。
- [ ] 明確保留舊 consumer 對未知 optional 欄位的相容行為。

## Task 2：交付 mode 與 evidence contract（RED）

- [ ] 測試 `inline`、`snapshot`、`note_fetch`、`ineligible`、`failure` 的互斥語意；snapshot/materialized ready/offer 不等於 content-returned。
- [ ] 測試 inline/context-delivered 不增加 read；只有 tool/provider 成功返回內容才可記 content-returned；note fetch 能表達 attempt → returned/failed → applied；缺事件不推導成功。
- [ ] 將工具中立事件與既有 strict KPI 對帳，不能改分母或把 inline 計入 read。

## Task 3：Hippo core implementation（GREEN）

- [ ] 以最小 diff 實作 payload builder、capability-aware delivery 與 bounded intent retrieval。
- [ ] provider/permission failure 保留分類證據並 fail closed；不新增 global allow-all 或 fallback 授權。
- [ ] 補單元/契約測試，確認公開 fixtures/reports 使用合成 ID、去識別路徑且不含 secrets；runtime payload 僅保留必要 bounded attribution metadata。

## Task 4：Cortex host adapter handoff

- [ ] 由 Cortex #857 依本契約建立 thin adapter；只映射 host capability 與現有 routing，不複製 Hippo core。
- [ ] 以正式 `cortex work intake` 建立持久 work item/run；若 system provider、registration 或 hard gate 拒絕，保留 CLI 證據並停止，不手改 durable state。
- [ ] 驗證 Hippo 未安裝或 payload provider 不可用時，既有 dispatch 仍依原有 fail-closed 行為回報。

## Task 5：canary 與 utility trial

- [ ] 每個 delivery/capability path 收集至少 5 筆 canary，記錄 attempt、return、failure、applied 與 authorization outcome。
- [ ] 以 eligible authorized retrieval 為分母，達到 >=95% 才進 utility trial；缺少 per-job authorization evidence 的資料列不能算成功。
- [ ] utility trial 另報實際效用，不變更 strict KPI，也不把未授權或 inline 視為 read。

## Task 6：verification gates

- [ ] `python3 -m pytest tests/ -q`。
- [ ] `python3 -m policy_check --repo .` 無 failure。
- [ ] `openspec validate --all --strict` 通過。
- [ ] PR review 確認跨 repo dependency、security boundary、KPI 語意與 redaction 均有證據；runtime completion 另行驗證。
