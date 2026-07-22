# External headless profile 矩陣

這份矩陣描述目前 release candidate 的 runtime contract。Hippo 不提供 HTTP/TCP
provider client，也不保存 API key、OAuth、provider URL 或 credential env-name；
登入與 launcher 由外部 CLI 自己負責。

| tier | profile | traits / task class | default model / effort | tokenized headless argv | 狀態 |
|---|---|---|---|---|---|
| 1 | `claude` | judge、reasoner / atomization、title | `sonnet` / `high` | `claude --model {MODEL} --effort {EFFORT} --safe-mode ... --tools '' ... --print` | 內建 tools/MCP/customizations 停用；仍須 service-effective live probe |
| 1 | `codex` | judge、reasoner / atomization、title | `gpt-5` / `high` | `codex exec --model {MODEL} -c model_reasoning_effort=high ... --ignore-user-config --disable shell_tool -` | 固定 high 映射符合目前 CLI；其他 effort 必須在 argv 明確映射後再 probe |
| 2 | `agy` | fast、responsive / title | `default` / `medium` | `agy --model {MODEL} --effort {EFFORT} --mode plan --sandbox --print` | 原生 CLI 無可證明的 zero-tool flag，預設不進 Dream atomization eligible set |
| 2 | `cg` | heavy-implementation、fast / atomization、title | `default` / `high` | `cg --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；alias-only 或 zero-tool 契約未證實時不得啟用 |
| 3 | `co-gem` | low-cost、fallback / atomization、title | `local` / `low` | `co-gem --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；本機 launcher headless smoke 通過後才可啟用 |
| 3 | `claude-gem` | low-cost、fallback / atomization、title | `local` / `low` | `claude-gem --model {MODEL} --effort {EFFORT} --headless --stdin` | 預設 disabled；本機 executable/契約驗證後才可啟用 |
| 3 | custom local | operator-defined traits/task class/model/effort | operator-defined | tokenized argv；不可含 shell wrapper 或 prompt token | 需 operator 以 profile manifest 配置 |

Router 依 `(tier, priority, profile id)` 決定順序，整個 session 使用同一份 frozen
prompt；每次最多 6 attempts / 6 agent calls、每 chunk 300 秒，失敗 profile 進
circuit cooldown。只允許明確的失敗類別 fallback；安全設定錯誤不會降級繞過。成功
但使用 Tier 2/3 時 provenance 會記 `degraded-success` 與先前 attempts。

所有 profile 必須使用 `shell=False` 的 tokenized argv；prompt 只走 stdin。`{PROMPT}`、
shell alias/function、shell metacharacter、`--yolo`、`--autopilot`、permission bypass、
tool/MCP/remote fallback 均拒絕。child env 是固定 allowlist，外部 launcher 可在
Hippo 邊界外處理認證。

每個 profile 另有明確 `enabled` gate；停用 profile 會在 executable probe 前即標成
`ineligible/disabled`，不消耗 agent call。Provenance 同時記錄 tier 與 tier 內 priority。

## 驗證邊界

- `tests/test_external_agent_profiles.py` 與 backend matrix 測試覆蓋 tier ordering、
  safety rejection、bounded fallback、cache separation 與 minimal env。
- `tests/test_atomizer_llm_live.py` 的真 CLI probe 只有在 operator 明確設定 live gate
  時執行；未執行不會被標成 passed。
- service-effective eligibility、每個 profile 的 live smoke、installed hook/service
  chain、三次 systemd soak 與 consumer `offered → Read` 仍是 release readiness matrix
  的待驗證 gates。
