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

## LLM backend 覆寫鏈

- **共用設定源**：`agent_exec.command` 與 `agent_exec.upstream_url` 由
  `paulshaclaw/memory/atomizer/atomizer.yaml` 起始，並可用
  `~/.config/paulshaclaw/atomizer.override.yaml` 覆寫。
- **臨時熱切換**：只想改 upstream，不想改本機 override 檔時，可設
  `PSC_CLAUDE_GEMMA4_UPSTREAM_URL`；它會蓋過 config 檔裡的
  `agent_exec.upstream_url`。
- **影響面**：SkillOpt rollout、`psc memory atomize --promoter llm` 與
  importer title 生成共用同一組 backend 設定；judge command 仍獨立由
  `skillopt.yaml` 控制。

### 替換 backend 步驟

1. 在 `atomizer.override.yaml` 改 `agent_exec.command` 指到新 wrapper / CLI。
2. 若 backend upstream 也改了，同步設定 `agent_exec.upstream_url`。
3. 只做短期切換時，改設 `PSC_CLAUDE_GEMMA4_UPSTREAM_URL` 即可，不必改檔。
4. 重新跑 `psc memory atomize ...` 或 `psc memory skillopt run ...` 驗證新路徑。

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
