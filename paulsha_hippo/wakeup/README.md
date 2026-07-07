# Wakeup brief builder

`paulshaclaw.memory.wakeup` 是 Stage 2 的 session-start 與 pre-compact 注入模組，負責為 agent 生成每專案的啟動摘要（wake-up brief）。

## 核心行為

- **Session-start injection**：在新 session 啟動時透過 `additionalContext` 注入 per-project brief。
  - Claude 使用 `SessionStart` hook（`claude_session_start.py`）
  - Copilot 使用 `sessionStart` hook（`copilot_session_start.py`）
  - 直接模組／CLI 路徑：`psc memory wakeup`

- **PreCompact capture**：在 context 壓縮時將 session 快照送入 importer，設 `capture_scope=pre_compact`。
  - Claude 使用 `claude_precompact.py`
  - Copilot 使用 `copilot_precompact.py`

## 實作特性

- **Fail-open**：任何錯誤都不會阻斷 session 啟動或 compaction；最壞情況是回傳空 brief。
- **Deterministic builder**：`builder.py` 的 `build_brief` 不使用 wall-clock（`now` 由呼叫端決定），純讀取、不修改狀態。
- **Read-only**：wake-up builder 只讀 MOC 與 knowledge slices，不寫任何檔案。
- **Reuse foundation**：
  - `project_resolver`：專案判定邏輯複用 importer 模組
  - `lifecycle` / `retrieval_set`：複用 ledger 模組的生命週期與檢索輔助
  - T7 MOC：MOC frontmatter 與檔案格式對齊 Stage 2 T7 規範
  - Importer flow：PreCompact hook 將 session 打包為 queue payload，透過 `importer.cli ingest` 入庫

## CLI 範例

```bash
# 手動生成 wake-up brief（指定專案）
python3 -m paulshaclaw.memory.cli memory wakeup \
  --memory-root ~/.agents/memory \
  --project paulshaclaw \
  --now "2025-01-15T12:00:00Z"

# 自動判定專案（從 cwd）
python3 -m paulshaclaw.memory.cli memory wakeup \
  --cwd ~/prj_pri/paulshaclaw

# 調整檢索參數
python3 -m paulshaclaw.memory.cli memory wakeup \
  --project my-repo \
  --k 12 \
  --char-budget 10000
```

## Hooks 使用場景

- **Session start**：hook 從 stdin 讀取 session payload（包含 `cwd`），透過 `project_resolver` 判定專案後生成 brief，將 brief 寫入 `additionalContext` 並輸出 JSON 至 stdout；Agent 平台在啟動時注入此 context。
- **PreCompact**：hook 從 stdin 讀取 session snapshot，寫為 `runtime/queue/<tool>__<session-id>.json` 並觸發 importer 背景執行；importer 解包後依原子化與分類流程入庫至 `knowledge/`。

## 配置

- **Memory root**：預設 `~/.agents/memory`，可由 `PSC_MEMORY_ROOT` 環境變數覆寫。
- **Hooks venv**：`~/.agents/memory/hooks/.venv` 供 hooks 使用的 Python 環境，安裝方式見 `hooks/install.sh`。

## Guardrails

- **Fail-open for retrieval**：`build_brief` 遇到不可讀檔案或不合規 frontmatter 時跳過該檔案，不中斷流程。
- **Fail-open for hooks**：hooks 任何異常都只記錄至 `log/hooks.log`，返回 exit 0，不阻斷 session 生命週期。
- **Project boundary**：只讀取 `project` frontmatter 與目錄結構匹配的 slices，避免跨專案洩漏。
- **No wall-clock in builder**：`now` 參數由呼叫端決定，builder 內部不調用 `datetime.now()`，確保相同輸入產生相同輸出（可測試、可重現）。
