---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

## Lifecycle mapping

- Active change：`openspec/changes/issue-18-luna-closeout-followup-v7`
- Archived change：`openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7`
- Manager archive commit：`cd10310b6c1a1f8c70d5d9b61fa541b8a5812676`
- Metadata：`created: 2026-07-27`；archive 目錄 date：`2026-07-26`
- Post-archive canonical spec sync：`openspec/specs/stage2-memory-usage-telemetry/spec.md`

## ADDED Requirements

### Requirement: v7 defines recovery authority only

Issue #18 Luna recovery follow-up is governed by `issue-18-luna-closeout-followup-v7` planning artifacts only and SHALL NOT introduce runtime, testing, CI, versioning, or changelog behavior changes.

- v7 artifacts are (前七件 authority + changelog，共八件)：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- 前七件 authority 檔保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 不使用 frontmatter。
- `origin/main` 為 pre-archive base，v6 candidate 不得作為 ancestry。

#### Scenario: authority-only planning

- **WHEN** v7 pre-archive candidate is assembled
- **THEN** only the files listed above are modified in the v7 pre-archive candidate.
- **AND** no runtime code, tests, CI workflow, `VERSION`, or changelog behavior is modified in the v7 pre-archive candidate.

### Requirement: v7 supersession trace

Issue #18 follow-up SHALL record that `workflow-456550b33c4dc592b496` superseded `issue-18-luna-closeout-followup-v6` candidate `aa634960eff6d8cd1f97d42732556a8983ab8129`（原因：未含 `changelog.d`，PR-aware R-09 not satisfiable in v6 frozen scope）。
- Operator evidence hash: `a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`（證據原始位於 Cortex operator state，不納入 repo）。

#### Scenario: supersession record

- **WHEN** v7 authority is evaluated
- **THEN** `workflow-456550b33c4dc592b496` SHALL be treated as superseding `issue-18-luna-closeout-followup-v6`.
- **AND** operator evidence hash `a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e` MUST be recorded in repo artifacts.

#### Scenario: authority transition

- **WHEN** v7 follow-up is assembled
- **THEN** v6 is recognized as superseded and v7 becomes the active closeout authority.
- **AND** v7 artifacts only reference the evidence hash above（不記錄 shareable 路徑）。

### Requirement: lifecycle transition recording

- `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7` 為 archive destination。
- `openspec/specs/stage2-memory-usage-telemetry/spec.md` 為 post-archive canonical spec sync，不回溯擴張 pre-archive 8-file card。

#### Scenario: lifecycle trace

- **WHEN** active change `openspec/changes/issue-18-luna-closeout-followup-v7` 被 archived
- **THEN** manager commit `cd10310b6c1a1f8c70d5d9b61fa541b8a5812676` SHALL be traceable from artifacts as the move event.
- **AND** `created: 2026-07-27` SHALL remain change metadata, while `2026-07-26` SHALL remain archive directory lifecycle date。

### Requirement: stage2 telemetry semantics are preserved

The follow-up SHALL preserve the accepted stage2 telemetry meanings from `issue-18-consumption-funnel-closeout`:

- `session citation`
- `unique-slice coverage`
- `offered-to-read conversion`
- `applied` (explicit acknowledgement, not a read/citation proxy)

#### Scenario: no semantic drift

- **WHEN** v7 artifacts are built into candidate
- **THEN** the four telemetry meanings above stay identical in wording and scope.
- **AND** no change to applied semantics.
