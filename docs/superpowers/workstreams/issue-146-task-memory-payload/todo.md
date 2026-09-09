---
status: accepted
work_item: issue-146-task-memory-payload
---

# issue-146-task-memory-payload / todo

正式規劃來源：

- spec — `docs/superpowers/specs/2026-09-07-issue-146-task-memory-payload-spec.md`
- design — `docs/superpowers/specs/2026-09-07-issue-146-task-memory-payload-design.md`
- plan — `docs/superpowers/plans/2026-09-07-issue-146-task-memory-payload.md`
- issue — [hamanpaul/paulsha-hippo#146](https://github.com/hamanpaul/paulsha-hippo/issues/146)
- host adapter dependency — [hamanpaul/paulsha-cortex#857](https://github.com/hamanpaul/paulsha-cortex/issues/857)

## Tasks

- [x] payload envelope/schema 與 bounded intent candidate contract（RED/GREEN）
  - [x] RED：新增 issue #146 契約回歸測試，固定 schema_version、bounded intent、最多三筆 candidates、deterministic ordering、空候選、provider unavailable、未授權候選與 redaction。
  - [x] GREEN：新增 `paulsha_hippo.task_memory_payload`，落地 envelope builder/validator，固定最多三筆、授權過濾、deterministic ordering 與 shareable-safe redaction。
- [x] capability-aware delivery modes 與 tool-neutral evidence（RED/GREEN）
  - [x] RED：新增 issue #146 契約回歸測試，固定 inline、snapshot、note_fetch、failure 的 evidence 語意，以及 returned/failed/applied 的可驗證事件邊界。
  - [x] GREEN：新增 `summarize_delivery_outcome()`，明確保留 inline/snapshot 不計 read、無 returned 不推導成功、returned+applied 才算 applied。
- [x] strict KPI compatibility、inline-not-read 與 fail-closed 測試
  - [x] RED：新增 issue #146 契約回歸測試，固定 inline 與 snapshot 不得計入 read、缺少 returned 不得推導成功，且 applied 不可單獨升格為 read。
- [x] Hippo core 最小實作與 shareable redaction
- [ ] Cortex thin adapter 依 #857 沿現行 routing 接入
- [ ] 每 path 至少 5 筆 canary，eligible authorized retrieval >=95%
- [ ] 通過第一道 gate 後才執行 utility trial
- [ ] pytest、policy_check、OpenSpec strict 與跨 repo review gate

## Blockers / dependencies

- [ ] Cortex #857 adapter 尚未完成；本 repo 先持久化可審查契約，不把 runtime 或 dispatch 宣稱完成。
- [ ] system provider/registration/hard gate 是否允許正式 intake，須以現行 Cortex CLI 證據為準，不透過手動 state 或 infra 修改繞過。
