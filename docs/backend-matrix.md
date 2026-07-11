# Backend preset 矩陣（#10）

> registry 真源：`paulsha_hippo/backends.py`（`PRESETS`，契約見
> `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.5）。
> 本文件記錄各 preset 的 argv 契約、doctor probe、前置條件與實測狀態
> （基線 2026-07-10）。機制：argv presets 全走 custom-argv 機制——prompt 由
> stdin 餵入、stdout 取回輸出（`AgentExecClient`）；HTTP 檔位走
> `HttpAgentClient`。機制零新增。

| preset | argv template | doctor probe | 前置條件 | 實測狀態（2026-07-10） |
|---|---|---|---|---|
| `claude-headless` | `claude -p` | `claude --version` | Claude Code 已登入 | ✓ v0.1.0 既有已驗檔位（原生執行檔，無 node PATH 問題） |
| `codex-headless` | `codex exec --skip-git-repo-check --sandbox read-only --color never -` | `codex --version` | Codex CLI 已登入 | ✓ stdin→stdout round-trip：stdout 僅含 final message，log 全走 stderr |
| `copilot-headless` | `copilot -s --no-color` | `copilot --version` | Copilot CLI 已登入 | ✓ stdin 為唯一 prompt 來源。⚠ 帶非空 `-p` 時 stdin 注入不可靠（實測內容丟失、agent 徘徊），preset 刻意不用 `-p` |
| `gemini-headless` | —（unavailable；候選未驗證：`gemini -p "執行 stdin 提供的任務指示"`，僅由 `--help` 推得、不入 registry template） | —（unavailable 宣告層短路） | 升級前提見下節 | ✗ unavailable：無成功 stdin→stdout round-trip 實證——2026-07-10 實測 `--version` rc=0、headless 呼叫 rc=41（selectedType=vertex-ai 無 `GOOGLE_CLOUD_PROJECT`/`GOOGLE_API_KEY` env）；依 spec §8「接不上就標 unavailable + 回報，不猜 argv」。`init` 選單顯示但選了 rc 2；不在 smoke 矩陣 |
| `antigravity-headless` | —（未確認） | — | — | ✗ unavailable：命令契約未確認（spec §2 非目標）；`init --backend` 選單顯示但選了會 rc 2 |
| `openai-compatible` | —（HTTP） | —（integration smoke） | `base_url` 必填；key 一律走 `api_key_env`（config 不放值） | env-gate smoke：`HIPPO_SMOKE_OPENAI_BASE_URL`（見 `tests/test_openai_smoke_integration.py`） |
| `custom-argv` | 使用者自訂 | — | argv[0] 建議絕對路徑 | ✓ 既有機制（預設 gemma4 wrapper 沿用） |

## unavailable preset 升級前提（gemini-headless／antigravity-headless）

翻 `available=True` 的必要前提（缺一不可，同一 PR 完成）：

1. 真實認證備妥後，以候選 argv 完成**一次成功的 stdin→stdout round-trip**
   （rc=0、stdout 取回可解析回覆本文），並把實測記錄更新進上表。
2. registry（`paulsha_hippo/backends.py`）同步：`argv_template` 填入實測定案
   argv、`available=True`。
3. 同 PR 補對應 live smoke（`tests/test_atomizer_llm_live.py`）——
   `SmokeMatrixCoverageTests` 強制「available 的 argv preset 必在 smoke 矩陣」，
   漏補即 FAIL。

實測證據記錄（gemini-headless，2026-07-10）：`gemini --version` rc=0；headless
round-trip **失敗 rc=41**——本機認證 selectedType=vertex-ai 而無對應 env
（`GOOGLE_CLOUD_PROJECT`/`GOOGLE_API_KEY`）。候選 argv 僅由 `--help` 文字
（`-p`：「Appended to input on stdin (if any)」）推得，無任何成功實證——依
spec §8 風險表「不猜 argv」標 unavailable。antigravity-headless：執行檔不存在、
命令契約未確認（spec §2 非目標）。

## systemd service 環境注意

- `codex`／`gemini` 是 node script（`#!/usr/bin/env node`）：即使 config 寫了
  絕對路徑 argv[0]，systemd --user service 的 PATH 沒有 node 目錄時仍會啟動
  失敗。解法：service unit 加 `Environment=PATH=...`（含 node bin 目錄）或改用
  self-contained backend（`claude-headless`／`openai-compatible`）。
  `hippo doctor` 的 preset probe 以 service-effective PATH 執行，能直接暴露
  這類故障。
- 蒸餾子程序一律帶 `HIPPO_SELF_SESSION=1`（`agent_exec` 注入）——三家 CLI 的
  hippo hooks 讀到即跳過 queue write，不會遞迴自捕捉。

## smoke 執行方式

    # 三 available preset 真蒸餾（claude/codex/copilot；probe 失敗者 skip 並回報
    # 原因；unavailable preset 不在矩陣，見上節升級前提）
    PSC_ATOMIZE_LIVE=1 python3 -m pytest tests/test_atomizer_llm_live.py -v -s -ra

    # openai-compatible 真端點（integration profile）
    HIPPO_SMOKE_OPENAI_BASE_URL=<endpoint> HIPPO_SMOKE_OPENAI_MODEL=<model> \
    python3 -m pytest tests/test_openai_smoke_integration.py -v -s

    # mock 情境矩陣（散文包 JSON／截斷／non-zero／timeout；一般 CI 內建）
    python3 -m pytest tests/test_atomizer_backend_matrix.py -v
