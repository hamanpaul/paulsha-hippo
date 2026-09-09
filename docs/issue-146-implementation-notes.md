# Issue #146 implementation notes

此檔承接可變的實作說明，避免修改已凍結的 planning authority。

## RED coverage

- payload envelope/schema 與 bounded intent candidate contract：新增 issue #146 契約回歸測試，固定 `schema_version`、bounded intent、最多三筆 candidates、deterministic ordering、空候選、provider unavailable、未授權候選與 redaction。
- capability-aware delivery modes 與 tool-neutral evidence：新增 issue #146 契約回歸測試，固定 `inline`、`snapshot`、`note_fetch`、`failure` 的 evidence 語意，以及 `returned`/`failed`/`applied` 的可驗證事件邊界。
- strict KPI compatibility、inline-not-read 與 fail-closed 測試：新增 issue #146 契約回歸測試，固定 inline 與 snapshot 不得計入 read、缺少 `returned` 不得推導成功，且 `applied` 不可單獨升格為 read。

## GREEN landed in Hippo

- `paulsha_hippo.task_memory_payload` 已落地 envelope builder/validator，固定最多三筆、授權過濾、deterministic ordering 與 shareable-safe redaction。
- `summarize_delivery_outcome()` 已固定 inline/snapshot 不計 read、無 `returned` 不推導成功、`returned`+`applied` 才算 applied。

## Remaining pre-archive work

- Cortex thin adapter 仍由 hamanpaul/paulsha-cortex#857 沿現行 routing 接入。
- 每個 delivery/capability path 仍需至少 5 筆 canary，且 eligible authorized retrieval >=95% 後才進 utility trial。
- Manager 端的 authoritative preflight、跨 repo review 與 archive/merge 動作不在本檔宣稱完成。
