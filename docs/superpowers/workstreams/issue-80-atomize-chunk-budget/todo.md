---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# issue-80-atomize-chunk-budget / todo

規劃三件套：

- spec — `docs/superpowers/specs/2026-07-28-issue-80-atomize-chunk-budget-spec.md`
- design — `docs/superpowers/specs/2026-07-28-issue-80-atomize-chunk-budget-design.md`
- plan — `docs/superpowers/plans/2026-07-28-issue-80-atomize-chunk-budget.md`

## Tasks

- [ ] session 預算隨 chunk 數縮放（600s 為下限、240s/chunk、1800s 上限；per-call 300s 不動）
- [ ] profile 宣告 `max_session_chunks`，超出者以既有 ineligible 路徑讓路且不耗 agent call
- [ ] 已驗證的 chunk 成果跨 profile 保留與續跑，provenance 誠實記載 per-chunk profile
- [ ] changelog.d 碎片、全套 pytest、policy_check、openspec strict validate

## Blockers

- [ ] 無

## 合併後 runtime 待辦（不在 PR diff 內）

- [ ] 同步使用者 live config `~/.config/paulsha-hippo/config.yaml` 補 tier-1 的 `max_session_chunks`
- [ ] 觀察後續有 ingress 的 timer cycle 是否產出 accepted atom（AR-11 的前置條件）
