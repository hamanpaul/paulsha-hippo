---
status: accepted
work_item: issue-146-task-memory-payload
---

# issue-146-task-memory-payload tasks（pre-archive）

## Tasks

- [x] 將 active change 範圍固定在 accepted plan 已落地的 Hippo core pre-archive 契約：payload schema、bounded intent retrieval、最多三筆 candidates、shareable-safe redaction，以及 delivery/read semantics。
- [x] RED：新增契約測試，固定 schema version、bounded intent、最多三筆 candidates、deterministic ordering、空候選、provider unavailable、未授權候選、redaction，並保留未知 optional 欄位的 consumer 相容性。
- [x] RED repair：將 truncation fixture 擴成四筆 authorized 候選，明確驗證 deterministic first-three truncation，不再是三選三 no-op。
- [x] GREEN：新增 `paulsha_hippo.task_memory_payload` 的 builder/validator 與 `summarize_delivery_outcome()`，固定只有 `returned` 才算 read、只有 `returned`+`applied` 才算 applied，inline/snapshot 不得升格為 read。
- [x] Governed preflight repair：runtime-health 測試固定改用 `/` 這個穩定且非暫存的既有 cwd fixture，避免 disposable HOME／temp checkout 被誤判為 `cwd-temp-worktree`。
- [x] Governed preflight repair：`stage2_integration_check.sh` 的 MOC fixture 改放進 EXIT 會清掉的 `TMP_DIR`，並讓 `tests/test_skillopt_valset.py` 清掉空的 `.test-work/skillopt-valset` 父目錄，避免 pytest 後留下 worktree residue。
- [x] 新增本次 pre-archive repair 需要的精確命名 changelog 片段 `changelog.d/issue-146-task-memory-payload.md`，且 `VERSION` 維持不變。
- [x] 保持 `openspec/changes/issue-146-task-memory-payload` 為 active pre-archive artifacts；archive、merge、issue closure 與 manager 後續動作仍 pending。
- [x] `python3 -m pytest tests/ -q`。
- [x] `python3 -m policy_check --repo .`。
- [x] `openspec validate --all --strict`。
- [x] Post-archive repair：`_sanitize_public_text()` 改為先做 `redact_secret_text()` 再 bounded truncate，避免 secret 在截斷邊界被腰斬後逃過 redaction。
- [x] Post-archive repair：`.cortex/work-items.yaml` 的 proposal path 改指向 archive 後實際存在的 `openspec/changes/archive/2026-09-09-issue-146-task-memory-payload/proposal.md`。

Remaining Cortex adapter、canary、utility trial 與 cross-repo handoff follow-up
另記於 `docs/issue-146-implementation-notes.md`，避免 active OpenSpec `tasks.md`
以未完成 checkbox 混入本次 pre-archive candidate。
