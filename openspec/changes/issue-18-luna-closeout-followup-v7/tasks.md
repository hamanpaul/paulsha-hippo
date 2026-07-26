---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna closeout follow-up-v7 tasks

## Context

- `workflow-456550b33c4dc592b496` 已將 `issue-18-luna-closeout-followup-v6` 候選 `aa634960eff6d8cd1f97d42732556a8983ab8129` 標記 `superseded`，原因為未含 `changelog.d` fragment 而觸發 PR-aware R-09。
- operator evidence hash：
  - `a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`
  - 原始證據位於 Cortex operator state，不寫入 repo candidate。

## Scope

- v7 pre-archive candidate 僅含 8 件 tracked 檔案（前七件 authority + `changelog.d`）：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- `origin/main` 為 base；不得將 v6 candidate 視為 ancestry。
- 前七件 authority 檔保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 不使用 frontmatter。

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

- [x] `openspec validate issue-18-luna-closeout-followup-v7 --strict`
- [x] `git diff --check origin/main..HEAD`
- [x] `python3 -m policy_check --repo .`
- [x] `python3 -m pytest -q tests`
- [x] PR-aware preflight
- [ ] `agy/gemini-3.6-flash-high` verification
- [ ] `agy/gemini-3.6-flash-high` code review
- [ ] `agy/gemini-3.6-flash-high` adversarial review

### BLUE（final review）

- [ ] `codex/gpt-5.6-luna(max)` final exact-head review (`model_reasoning_effort=max`)
- [ ] `codex/gpt-5.6-luna(max)` final exact-head 模型鏈：確認 model chain 明載為 `codex/gpt-5.3-codex-spark` + `agy/gemini-3.6-flash-high` + `codex/gpt-5.6-luna(max)`。

### LUNA / ship（後續）

- [ ] manager 進行 archive active OpenSpec
- [ ] manager 進行 policy commit
- [ ] manager 提 PR（`Closes #18`，Cortex-only merge）
- [ ] manager 完成 issue close

### Adversarial criteria

- `未處置的缺陷/缺口`：FAIL
- 已承認、影響有界且已列管殘餘風險：不單獨 FAIL；若 reviewer 反對，需以具體影響分析回應
