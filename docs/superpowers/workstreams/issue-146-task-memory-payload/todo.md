---
status: accepted
work_item: issue-146-task-memory-payload
---

# issue-146-task-memory-payload / todo

正式規劃來源：

- spec — `docs/superpowers/specs/2026-09-07-issue-146-task-memory-payload-spec.md`
- design — `docs/superpowers/specs/2026-09-07-issue-146-task-memory-payload-design.md`
- plan — `docs/superpowers/plans/2026-09-07-issue-146-task-memory-payload.md`
- issue — [hamanpaul/paulsha-hippo#146](https://github.com/hamanpaul/paulsha-hippo/issues/146)
- host adapter dependency — [hamanpaul/paulsha-cortex#857](https://github.com/hamanpaul/paulsha-cortex/issues/857)

## Tasks

- [x] payload envelope/schema 與 bounded intent candidate contract（RED/GREEN）
- [x] capability-aware delivery modes 與 tool-neutral evidence（RED/GREEN）
- [x] strict KPI compatibility、inline-not-read 與 fail-closed 測試
- [x] Hippo core 最小實作與 shareable redaction
- [ ] Cortex thin adapter 依 #857 沿現行 routing 接入
- [ ] 每 path 至少 5 筆 canary，eligible authorized retrieval >=95%
- [ ] 通過第一道 gate 後才執行 utility trial
- [x] `python3 -m pytest tests/ -q`
- [x] `python3 -m policy_check --repo .`
- [x] `openspec validate --all --strict`
- [ ] 跨 repo review gate

## Blockers / dependencies

- [ ] Cortex #857 adapter 尚未完成；本 repo 先持久化可審查契約，不把 runtime 或 dispatch 宣稱完成。
- [ ] system provider/registration/hard gate 是否允許正式 intake，須以現行 Cortex CLI 證據為準，不透過手動 state 或 infra 修改繞過。
