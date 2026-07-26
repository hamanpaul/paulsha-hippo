---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna close-out follow-up-v7 / todo

## Context

- `workflow-456550b33c4dc592b496` 已將 `issue-18-luna-closeout-followup-v6` 候選 `aa634960eff6d8cd1f97d42732556a8983ab8129` 標記 `superseded`，原因為未含 `changelog.d` fragment（PR-aware R-09）。
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
- v7 以 `origin/main` 為 base；v6 candidate 不作 ancestry。
- 排除 runtime code、tests、CI、`VERSION`、`CHANGELOG`、v2-v6 artifacts。

## Lifecycle mapping

- Active change：`openspec/changes/issue-18-luna-closeout-followup-v7`
- Archived change：`openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7`
- Manager archive commit：`cd10310b6c1a1f8c70d5d9b61fa541b8a5812676`
- Metadata：`created: 2026-07-27`；archive 目錄日期：`2026-07-26`

## Tasks

### RED（planning establishment）

- [x] 建立 v7 七件 planning artifacts 並統一 identity：
  - `status: accepted`
  - `work_item: issue-18-luna-closeout-followup-v7`
- [x] 明列 v6 superseded 緣由、run id、candidate SHA、operator evidence hash。
- [x] 保留四個 stage2 telemetry 語意：
  - `session citation`
  - `unique-slice coverage`
  - `offered-to-read conversion`
  - `applied`（不做為 read/citation proxy）

### GREEN（build checks）

- [x] `openspec validate issue-18-luna-closeout-followup-v7 --strict`（active change）
- [x] `openspec validate --all --strict`（repair workspace 11/11；Cortex exact candidate 仍須重驗）
- [x] `git diff --check origin/main..HEAD`（已過，未發現尾端空白行告警）
- [x] `python3 -m policy_check --repo .`
- [x] `python3 -m pytest -q tests`
- [x] PR-aware preflight（含 `policy_check --repo . --pr-base-ref origin/main --pr-head-ref HEAD`）
- [x] `agy/gemini-3.6-flash-high` verification / code / adversarial（commit `23acb84` 三門完成）
- [ ] repaired exact candidate 須於 Cortex 重跑 `agy/gemini-3.6-flash-high` 三門
- [ ] `codex/gpt-5.6-luna(max)` final exact-head review（`model_reasoning_effort=max`，`commit 23acb84` FAIL，修復後待重跑）

### LUNA（post-archive）

- [x] manager `openspec archive` commit：`cd10310b6c1a1f8c70d5d9b61fa541b8a5812676` 已存在於 git history
- [ ] manager：PR #62 已建立；final policy/delivery gate、Cortex-only merge、issue close 尚未完成
