# paulsha-hippo code 遷移計劃（#125 Phase 1 §3）

> 基準：paulshaclaw main（含 facade #218、p1 Gap①③、G3 #219）；hippo main `88c7f8f` 骨架。
> 依據：paulshaclaw `docs/superpowers/specs/2026-07-06-memory-extraction-hippo-design.md` §5、openspec `memory-extraction-hippo` tasks §3。

## 遷移對照

| 來源（paulshaclaw） | 目的（paulsha-hippo） |
|---|---|
| `paulshaclaw/memory/<mod>/**` | `paulsha_hippo/<mod>/**`（攤平一層） |
| `paulshaclaw/lifecycle/**` | `paulsha_hippo/lib/lifecycle/**` |
| `paulshaclaw/memory/dream/idle.py` | `paulsha_hippo/lib/idle.py`（dream 改 import lib） |
| `paulshaclaw/memory/tests/**` | `tests/**`（併入 hippo tests） |
| `openspec/specs/stage2-*` | `openspec/specs/`（12 capabilities） |
| `scripts/claude-gemma4*`、`config/claude-gemma4-settings.json` | `examples/`（custom-argv 範例） |

偏差記錄：lib 第三包 `jsonl` 延後至 §4（主 repo manager_daemon 去重時抽）——機械平移優先，spec §3.2 的入會條件屆時補足。

## import 改寫規則

- `paulshaclaw.memory.` → `paulsha_hippo.`；`from paulshaclaw import memory` 無此型（已盤點）
- `paulshaclaw.lifecycle` → `paulsha_hippo.lib.lifecycle`
- `from paulshaclaw.config import paths` → `from paulsha_hippo import paths`（hippo 版 resolver）
- 字串級：`-m paulshaclaw.memory.importer.cli` → `-m paulsha_hippo.importer.cli`；grep 清零驗收

## 新元件（第二刀）

1. `paulsha_hippo/paths.py`：單一權威 resolver——`HIPPO_*` > `PSC_*`（deprecated stderr 警告）> config.yaml > `~/.agents/memory` 預設
2. `paulsha_hippo/hippo_config.py`：`~/.config/paulsha-hippo/config.yaml` + env 覆寫
3. CLI 去 `memory` 前綴層＋新命令：`init`、`doctor`（雙 root FAIL）、`install hooks|service`、`dream supervise`
4. 蒸餾三檔位 runner：`claude-headless`（preset argv=`claude -p`）、`openai-compatible`（stdlib http）、`custom-argv`（現 agent_exec 原樣）
5. installer：hooks（沿 install.sh 冪等語意）＋ service（systemd 偵測→user units；否→supervise 指引）

## 驗收

- hippo 全測試綠（平移 91 檔 + 骨架 6 + 新元件測試）
- `pip install -e .` 後 `hippo --version`／`hippo dream --help` 可用
- lib 隔離測試維持綠；`paulshaclaw` 字串 grep-zero（examples 除外）
- policy-check（R-21 shareable）0 fail
