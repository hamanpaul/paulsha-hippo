---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
---

# Issue #41 usage feedback loop v5 / todo

## Tasks

本清單是 v5 的 accepted execution plan。

## 權威檔目標（7 件，僅新增）

- `docs/superpowers/plans/2026-07-27-issue-41-usage-feedback-loop-v5-sonnet.md`
- `docs/superpowers/workstreams/issue-41-usage-feedback-loop-v5-sonnet/todo.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/.openspec.yaml`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/proposal.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/design.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/tasks.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/specs/stage2-memory-usage-feedback/spec.md`

## 執行前置

- [x] 已鎖定 builder：`claude/claude-sonnet-5`（effort high；一次初跑＋至多一次 repair）。
- [x] 已核定 `base_sha=b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`。
- [x] v5 完整承接 v4 七份 authority；切換 provider 不改產品 scope。
- [x] planning 階段只允許此 7 件 authority；實作與程式變更後續再提交。

## 目前狀態

- [x] planning authority 7 件建立。
- [ ] 實作（code）未開始。
- [ ] focused tests 未開始。
- [ ] full tests 未開始。
- [ ] policy/diff gates 未開始。
- [ ] 實作層審核未開始。

## 共識邊界

- 保留已接受 v4 契約：
  - 來源 `runtime/ledger/offered.jsonl` 與 `runtime/ledger/memory_usage.jsonl`
  - 僅 `(tool, logical session, slice_id)` 的 prior offer 後 `source=read`、非 applied、窗口內事件可計 read
  - unoffered/direct、cited/matched、malformed/future 不可計入
  - 以 `read_count`、`last_read_at` 聚合；`ttl = max(captured_at, active_since_ts, valid last_read_at)`
- v5 新增機械化門檻：
  - streaming iterator、bounded diagnostics、identity 空值保護、scanner no-zero-warning、`CHANGELOG` 文案修正、`read` 不 reactivation。

## 需求缺口（只列 BLOCKER/MAJOR）

- [ ] BLOCKER：I/O/UTF-8/single-line parse fail-soft 全流程
- [ ] BLOCKER：malformed/non-object/missing key/timestamp/window 規則與 fixed counter
- [ ] BLOCKER：`Path.read_text` 失敗與大檔 streaming regression
- [ ] MAJOR：module docstring/格式最小 churn
- [ ] MAJOR：legacy DB fallback、ranking stable、janitor priority retention detail
- [ ] MAJOR：scanner zero-warning gate（counter==0 不輸出）

## 審查與交付條件（v5）

- Builder 必須在同一 commit 中提交這 7 件 authority 與實作變更，並讓 reviewer 可直接 checkout 閱讀。
- Agy reviewer 必須先讀這套 frozen plan（7 件 authority）後，再做 code/spec/janitor/funnel 驗收。
- Agy BLOCKER/MAJOR 只列最多 8 條；未處置缺口 FAIL；有界殘餘風險不獨立 FAIL。
- Claude 第二次 build/repair 後仍未通過即停在 `needs_human`；不得自動建立 v6。
