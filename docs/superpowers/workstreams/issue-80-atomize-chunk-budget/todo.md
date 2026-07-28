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

## 派工環境前置（2026-07-28 實測）

- cortex 套件預設的 canonical planning identity `agy / "Gemini 3.1 Pro (High)"` 已失效：probe 以字面比對 `agy models` 輸出，而 agy 現在輸出 kebab id（`gemini-3.1-pro-high`），比不到即回 `model-not-listed`，令 workflow run 永遠停在 `define` / `needs_human`（cortex #255）。繞法是在 `~/.agents/config/paulsha/model-identities.yaml` 以真實 id 另宣告一個 agy planning identity。
- builder 身分由 `model-identities.yaml` 決定（workflow 路徑的 launcher 直接取 `identity.executor` 與 `identity.model_id`），沒有 `PSC_MANAGER_MODEL` 這個環境變數可 pin 模型。本批次的 builder 為 `codex / gpt-5.3-codex-spark`。
- `PSC_PREFLIGHT_CMD` 未設時 `cortex doctor` 報 FAIL，且 run 走到 verify/ship 會失敗；本機已設為 `policy-preflight --repo-visibility private`。
