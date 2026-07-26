---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna closeout follow-up-v7 plan

## Context

- `workflow-456550b33c4dc592b496` 已由 operator 將 `issue-18-luna-closeout-followup-v6` 的 candidate `aa634960eff6d8cd1f97d42732556a8983ab8129` 正式標記為 `superseded`，原因為 PR-aware R-09 缺失 `changelog.d` 所致（v6 frozen authority 僅允許七件 planning artifacts）。
- `workflow-456550b33c4dc592b496` 對應的 candidate 已完成 Spark build 與 Gemini 三門 review。
- operator 另存 evidence hash：
  - `a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`
  - 以上證據原始位於 Cortex operator state，不納入 shareable candidate；repo 僅保留 evidence hash。
- v7 以 `origin/main` 為交付基準，不以 v6 candidate 作為 ancestry 前提。

## Scope boundaries（v7）

- v7 pre-archive candidate 僅含以下八件 tracked 檔案（前七件 authority + `changelog.d`，不含 `.cortex` mapping）：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- 前七件 authority 檔保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 為治理/release note artifact，**不使用 frontmatter**（不代表 runtime changelog 行為）。
- 明確排除：runtime code、tests、CI、`VERSION`、`CHANGELOG`、v2-v6 artifacts、以及任何未列入上述範圍之檔案。

## Telemetry semantics（保留）

- `session citation`
- `unique-slice coverage`
- `offered-to-read conversion`
- `applied`（非 read/citation proxy）

## Model chain（required）

- build：`codex/gpt-5.3-codex-spark`
- verification / code / adversarial：`agy/gemini-3.6-flash-high`
- final exact-head review：`codex/gpt-5.6-luna`，`model_reasoning_effort=max`

## Tasks

### RED（planning establishment）

- [x] 建立 v7 planning authority 並統一 frontmatter：
  - `status: accepted`
  - `work_item: issue-18-luna-closeout-followup-v7`
- [x] 明列 v6 superseded 事實、run id、candidate SHA、operator evidence hash，並註明絕對路徑不進 shareable candidate 內容。
- [x] 列明四項 stage2 telemetry 語意保留。

### GREEN（post-build handoff）

- [ ] `openspec validate issue-18-luna-closeout-followup-v7 --strict`
- [ ] `git diff --check origin/main..HEAD`
- [ ] `python3 -m policy_check --repo .`
- [ ] `python3 -m pytest -q tests`
- [ ] PR-aware preflight（PR-aware）
- [ ] 完成 `agy/gemini-3.6-flash-high` 驗證、code、adversarial
- [ ] 完成 `codex/gpt-5.6-luna(max)` final exact-head review（`model_reasoning_effort=max`）
- [ ] 由 manager 於 Cortex flow 完成 archive、policy commit、PR、merge、close issue（含 `Closes #18`）
