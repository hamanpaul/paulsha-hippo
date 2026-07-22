# SkillOpt atomize layer

`paulshaclaw.memory.skillopt` 是 Stage 2 的離線最佳化模組，用來精煉
`paulshaclaw/memory/atomizer/skills/atomize-knowledge-slice.md`。

## Guardrails

- **Gate-protected**：只有候選 skill 在 validation set 上嚴格優於 baseline 才會覆寫；最壞情況是 skill 保持不變。
- **Fail-closed**：rollout、optimizer、judge 任一模型失敗都不會留下半套結果；skill 維持原樣。
- **Offline only**：本 change 不接到 dream / self-evolve loop。
- **Judge scope**：LLM judge 只評原子化品質（顆粒度、概念邊界、one-concept-per-slice、relations），**不**負責 project 判定；project 仍由 importer / `project_resolver` 決定。
- **Reference-only notes**：`~/notes` 只作 judge rubric，唯讀、不是 gold，也不會被寫入；`PersonalVault` 會被排除。

## Model roles

- **rollout**: atomizer `LLMPromoter`（預設沿用 atomizer agent command）
- **optimizer**: codex ACP（vendored adapter）
- **judge**: 可由 `~/.agents/config/skillopt.yaml` 指定的 agent command

## External CLI profiles

- **共用設定源**：`external_agents.profiles` 宣告 tier、traits、task class、model、effort 與 tokenized argv。
- **身份邊界**：SkillOpt rollout、`hippo atomize --promoter llm` 與 importer title 生成共用同一組外部 headless CLI router；OAuth/API key、endpoint 與 launcher lifecycle 不屬於 Hippo。
- **judge**：仍可由 `skillopt.yaml` 指定一個外部 CLI command，但 prompt 只走 stdin，且不繼承 parent environment。

### 替換 backend 步驟

1. 在 canonical `external_agents.profiles` 新增或調整一個外部 CLI profile。
2. 以 tokenized argv 指定模型與 effort；禁止 shell wrapper、prompt argv 與權限繞過旗標。
3. 重新跑 `hippo atomize ...` 或 `hippo skillopt run ...`，查看 bounded fallback/provenance。

## CLI

```bash
python3 -m paulshaclaw.memory.cli memory skillopt run --dry-run
python3 -m paulshaclaw.memory.cli memory skillopt run --budget 1
```

可選設定檔：`~/.agents/config/skillopt.yaml`

```yaml
judge_command:
  - python3
  - -m
  - judge.demo
alpha: 0.4
val_ratio: 0.2
min_project_sample: 2
judge_timeout: 600
```

若目前沒有 validation items，CLI 會明確提醒先跑 importer；若 inbox 已存在，也會提示目前 split 設定可能讓資料全部留在 train。
