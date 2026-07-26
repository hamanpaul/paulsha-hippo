---
status: accepted
work_item: issue-18-consumption-funnel-closeout
---

# Issue #18 closeout tasks（pre-archive）

## Tasks

- [x] Keep `openspec/changes/issue-18-consumption-funnel-closeout/.openspec.yaml`,
  proposal, design, `tasks.md`, and `specs/stage2-memory-usage-telemetry/spec.md` synced
  in this worktree（根據接受版本）。
- [x] Update `docs/cross-cli-capability-matrix.md` to document
  `hippo usage funnel --memory-root <path> --json` with explicit `session citation`,
  `unique-slice coverage`, `offered-to-read conversion`, and explicit `applied`
  semantics（不當作 read/citation proxy）。
- [x] Add/maintain focused regression test to reject stale placeholder wording and
  enforce matrix 契約（command + four 指標）在檔案與測試中一致。
- [x] Add the required `changelog.d/` fragment and keep `VERSION` unchanged.
- [x] Run focused tests for this closeout and `openspec validate
  issue-18-consumption-funnel-closeout --strict`（以結果更新為實際完成狀態）。
- [x] Keep `openspec/changes/issue-18-consumption-funnel-closeout`
  `proposal.md`, `design.md`, and `tasks.md` as active pre-archive artifacts，並以
  `docs/superpowers/plans/2026-07-26-issue-18-consumption-funnel-closeout.md`
  作為對照 plan（此 worktree 僅 claim pre-archive closeout 準備，archive 仍
  pending）。
