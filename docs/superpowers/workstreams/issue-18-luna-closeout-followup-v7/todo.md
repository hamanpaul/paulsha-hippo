---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna close-out follow-up-v7 / todo

## Context

- `workflow-456550b33c4dc592b496` 已將 `issue-18-luna-closeout-followup-v6` 候選 `aa634960eff6d8cd1f97d42732556a8983ab8129` 標記 `superseded`（PR-aware R-09 缺失 `changelog.d`）。
- 追溯證據保留 operator evidence hash：`a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`
  - 原始證據位於 Cortex operator state，不納入 shareable candidate。

## Scope boundaries

- v7 候選 tracking files（七件 authority + changelog，總計八件）：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- 前七件 authority 保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 不使用 frontmatter，僅為治理/release note artifact。
- v7 以 `origin/main` 為基準，不以 v6 candidate 為 ancestry。
- 排除 runtime code、tests、CI、`VERSION`、`CHANGELOG`、v2-v6 artifacts。

## Tasks

### RED（planning establishment）

- [x] 建立 v7 七件 planning artifacts 並統一 `status: accepted` / `work_item: issue-18-luna-closeout-followup-v7`。
- [x] 明列 v6 supersede 緣由、run id、candidate SHA、operator evidence hash，並聲明 operator-only evidence 不進 shareable 內容。
- [x] 明定 `session citation`、`unique-slice coverage`、`offered-to-read conversion`、`applied` 四語意不得退化（`applied` 非 read/citation proxy）。

### GREEN（build checks）

- [ ] `openspec validate issue-18-luna-closeout-followup-v7 --strict`（未完成）
- [ ] `git diff --check origin/main..HEAD`（未完成）
- [ ] `python3 -m policy_check --repo .`（未完成）
- [ ] `python3 -m pytest -q tests`（未完成）
- [ ] PR-aware preflight（未完成）
- [ ] `agy/gemini-3.6-flash-high` verification / code / adversarial（未完成）
- [ ] `codex/gpt-5.6-luna(max)` final exact-head（`model_reasoning_effort=max`）（未完成）

### LUNA（post-archive）

- [ ] manager：Cortex archive
- [ ] manager：policy commit
- [ ] manager：PR（`Closes #18`，Cortex-only merge）
- [ ] manager：issue close
