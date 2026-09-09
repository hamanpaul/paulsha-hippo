---
status: accepted
work_item: issue-146-task-memory-payload
---

# issue-146-task-memory-payload tasks

## Task 1: payload contract 與 shareable-safe envelope

- [x] RED：新增契約測試，固定 schema_version、bounded intent、最多三筆 candidates、deterministic ordering、空候選、provider unavailable、未授權候選與 redaction。
- [x] RED repair：將 truncation fixture 擴成四筆 authorized 候選，明確驗證 deterministic first-three truncation，不再是三選三 no-op。
- [x] GREEN：新增 `paulsha_hippo.task_memory_payload` 的 builder/validator，固定最多三筆、授權過濾、deterministic ordering 與 unknown optional fields 相容。

## Task 2: delivery mode 與 evidence/read semantics

- [x] RED：新增契約測試，固定 inline、snapshot、note_fetch、failure 的 evidence 邊界。
- [x] GREEN：新增 `summarize_delivery_outcome()`，固定只有 `returned` 才算 read、`returned`+`applied` 才算 applied，inline/snapshot 不得升格為 read。

## Task 3: 後續整合與驗證

- [ ] Cortex thin adapter 依 hamanpaul/paulsha-cortex#857 接入既有 routing。
- [ ] 每個 delivery/capability path 至少 5 筆 canary，eligible authorized retrieval >=95%。
- [ ] 通過第一道 gate 後才執行 utility trial。
- [x] Governed preflight repair：runtime-health 測試固定改用 `/` 這個穩定且非暫存的既有 cwd fixture，避免 disposable HOME／temp checkout 被誤判為 `cwd-temp-worktree`。
- [x] Governed preflight repair：`stage2_integration_check.sh` 的 MOC fixture 改放進 EXIT 會清掉的 `TMP_DIR`，並讓 `tests/test_skillopt_valset.py` 清掉空的 `.test-work/skillopt-valset` 父目錄，避免 pytest 後留下 worktree residue。
- [x] `python3 -m pytest tests/ -q`。
- [x] `python3 -m policy_check --repo .`。
- [x] `openspec validate --all --strict`。
- [ ] 跨 repo review gate。
