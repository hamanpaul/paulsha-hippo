---
status: accepted
work_item: issue-41-usage-feedback-loop-v5-sonnet
---

# Issue #41 usage feedback loop v5 / todo

## Tasks

本清單是 v5 的 accepted direct execution plan。

## 權威檔目標（7 件，僅新增）

- `docs/superpowers/plans/2026-07-27-issue-41-usage-feedback-loop-v5-sonnet.md`
- `docs/superpowers/workstreams/issue-41-usage-feedback-loop-v5-sonnet/todo.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/.openspec.yaml`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/proposal.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/design.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/tasks.md`
- `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet/specs/stage2-memory-usage-feedback/spec.md`

## 執行前置

- [x] 使用者已終止 Cortex lifecycle，改由主 Codex agent +
  Codex native subagent direct closeout。
- [x] 已核定 `base_sha=b4a317f9bfa38708eabbdb31e083dfc3b6e4c044`。
- [x] v5 完整承接 v4 七份 authority；切換 provider 不改產品 scope。
- [x] worktree / branch 寫入邊界固定為
  `feature/41-issue-41-usage-feedback-loop-v5-sonnet`。

## 目前狀態

- [x] planning authority 7 件建立。
- [x] initial candidate `6af69a4` 已建立，但 exact-candidate audit 判定未完成。
- [x] direct RED regressions 完成：focused run 為 `14 failed, 72 passed,
  1 subtests passed`，並確認均在 `6af69a4` call chain 失敗。
- [ ] Codex subagent GREEN implementation 完成。
- [ ] focused / full / installed-wheel tests 通過。
- [ ] OpenSpec / policy / diff / preflight-ci gates 通過。
- [ ] PR current-head review/checks 通過並 merge。

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
- [ ] BLOCKER：不得保留 ledger-wide row lists；只允許 iterator +
  compact aggregate state
- [ ] BLOCKER：future/out-of-window offer 與 `(unknown)` session/sl_id
  不得 cross-match
- [ ] MAJOR：module docstring/格式最小 churn
- [ ] MAJOR：boosted stable key 第二欄必須是 `base_score`
- [ ] MAJOR：index build 必須可注入 `usage_now/window_days`
- [ ] MAJOR：no-boost fast path 維持 legacy base-score stable order
- [ ] MAJOR：future `last_read_at` 不得延長 janitor TTL
- [ ] MAJOR：legacy DB fallback、janitor priority retention detail
- [ ] MAJOR：scanner zero-warning gate（counter==0 不輸出）

## 審查與交付條件（v5 direct）

- subagent 只寫 issue #41 worktree；主 agent 獨立逐條驗證 authority 與 RED。
- 未處置 BLOCKER/MAJOR 不得開 PR；既有 suite 綠燈不能取代缺口測試。
- local preflight-ci、GitHub current-head checks、review threads 與 mergeability
  全綠後才 merge。
- PR body 必須以 `Closes #41` 建立原生 closure link；merge 後驗證 issue
  closed 與 main merge ancestry。
