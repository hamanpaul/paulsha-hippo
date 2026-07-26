---
status: accepted
work_item: issue-18-luna-closeout-followup-v7
---

# Issue #18 Luna closeout follow-up-v7 proposal

## Requirements

- 本卡為 `issue-18-luna-closeout-followup-v7` planning authority，僅限規劃與治理交接，不牽涉 runtime/測試/CI/versioning/變更日誌行為。
- `workflow-456550b33c4dc592b496` 已將 v6 candidate `aa634960eff6d8cd1f97d42732556a8983ab8129` 正式標為 `superseded`，原因為 PR-aware R-09 需 `changelog.d` 卻不在 v6 frozen scope。
- 同一 candidate 通過 Spark build 與 Gemini 三門 review，但因前述缺口不得復用為 shipping authority。
- operator evidence 記錄（僅 operator 內部可見）：
  - repo 僅留 evidence hash：`a5bc3c12d2b9a545f4fada70ac767423836a281454a3af131a6bda3e0b33f77e`
  - 以上證據原始位於 Cortex operator state，不納入 shareable candidate。

## Model chain

- build：`codex/gpt-5.3-codex-spark`
- verification / code / adversarial review：`agy/gemini-3.6-flash-high`
- final exact-head review：`codex/gpt-5.6-luna(max)` 並附 `model_reasoning_effort=max`

## Scope

- v7 pre-archive candidate 只追蹤以下八件（前七件 authority + changelog）：
  - `docs/superpowers/plans/2026-07-27-issue-18-luna-closeout-followup-v7.md`
  - `docs/superpowers/workstreams/issue-18-luna-closeout-followup-v7/todo.md`
  - `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7/.openspec.yaml`
  - `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7/proposal.md`
  - `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7/design.md`
  - `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7/tasks.md`
  - `openspec/changes/archive/2026-07-26-issue-18-luna-closeout-followup-v7/specs/stage2-memory-usage-telemetry/spec.md`
  - `changelog.d/issue-18-luna-closeout-followup-v7.md`
- `origin/main` 為 base；v7 不承接 v6 candidate 作為 ancestry。
- 前七件 authority 檔保留 `status: accepted`、`work_item: issue-18-luna-closeout-followup-v7`；`changelog.d/issue-18-luna-closeout-followup-v7.md` 為治理/release note artifact，不使用 frontmatter，不是 runtime changelog consumption behavior。
- 排除 v2-v6 artifacts、runtime code、tests、CI、`VERSION`、`CHANGELOG`、`.cortex` mapping。

## Gates

- 未進行的 v7 pre-archive 工作卡包含：
  - `openspec validate issue-18-luna-closeout-followup-v7 --strict`
  - `git diff --check origin/main..HEAD`
  - `python3 -m policy_check --repo .`
  - `python3 -m pytest -q tests`
  - PR-aware preflight
- manager 後續再處理 archive / policy commit / PR / merge / close（含 `Closes #18`，Cortex-only merge）。
