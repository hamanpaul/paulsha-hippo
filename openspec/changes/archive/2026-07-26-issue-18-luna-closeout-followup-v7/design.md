---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna close-out follow-up-v7 design

## Design

本 v7 重新裁決只承接管理權責：取代 `issue-18-luna-closeout-followup-v6`，保留同一 closeout 事實鏈，並補齊 PR-aware R-09 所需 changelog fragment 的交付要求。

1. 授權切換

- `workflow-456550b33c4dc592b496` 已將 v6 candidate `aa634960eff6d8cd1f97d42732556a8983ab8129` 標為 `superseded`（缺少 R-09 規定的 `changelog.d`）。
- evidence hash 僅記錄於 repo 文件：`a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`。
- operator evidence 原始存放於 Cortex operator state，不得進 shareable candidate。

2. Artifact scope governance

- v7 pre-archive candidate scope 僅限七件 authority files + `changelog`（共八件）：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- `origin/main` 作為 base；不得以 `issue-18-luna-closeout-followup-v6` 作為 ancestry。
- 前七件 authority 檔保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 為治理/release note artifact，不使用 frontmatter。
- 明確排除：runtime code、tests、CI、`VERSION`、`CHANGELOG`、`.cortex` 映射、以及所有未列入本案 scope 的項目。

3. Lifecycle mapping

- Active pre-archive change：`openspec/changes/issue-18-luna-closeout-followup-v7`
- Archived change：`openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7`
- Manager archive commit：`cd10310b6c1a1f8c70d5d9b61fa541b8a5812676`（將 active change 搬到 archive）
- Metadata：`created: 2026-07-27`；archive date：`2026-07-26`
- Post-archive canonical spec sync：`openspec/specs/stage2-memory-usage-telemetry/spec.md`

4. Immutable telemetry semantics

- `session citation`
- `unique-slice coverage`
- `offered-to-read conversion`
- `applied`（不做為 read/citation proxy）

5. Model chain

- build：`codex/gpt-5.3-codex-spark`
- verification / code / adversarial：`agy/gemini-3.6-flash-high`
- final exact-head：`codex/gpt-5.6-luna(max)`，`model_reasoning_effort=max`

6. Handoff constraints

- v7 僅定義 planning authority；manager post-archive 可執行 archive、policy commit、PR（`Closes #18`）、merge、close issue，但這些在本 card 僅能紀錄 `cd10310...` commit 已存在，不可宣告完成或 final approval。
