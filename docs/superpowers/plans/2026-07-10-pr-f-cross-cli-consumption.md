# PR-F 跨 CLI 消費（#17 + #18）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓三家 CLI（claude-code / codex / copilot-cli）依實測能力消費記憶——有 prompt-time hook 就自動注入 shortlist、沒有就注入顯式 `hippo recall` 指引；並交付 `offered → read → applied` 漏斗（per-tool 分列、applied 顯式訊號、Claude 平台實證），同步修正 usage 文件漂移。

**Architecture:** 消費側全部收斂到既有的 `_shortlist_common.build_shortlist_and_record()`（bm25 檢索 → shortlist → offered 記錄，本來就 tool-agnostic）：新增 `hippo recall` CLI 作為跨 CLI consumer API；codex/copilot 的 SessionStart hook 改注入「顯式 recall 指引」（不再假裝 orientation 等同 task retrieval）；`applied` 為 agent 主動呼叫 `hippo usage mark-applied` 寫入 ledger 的顯式訊號，shortlist 尾行注入回報指引。capability matrix 以官方文件＋本機 probe 實測為據，決定各平台走 adapter 接線或 recall 指引。

**Tech Stack:** Python 3 stdlib（argparse / json / shlex / pathlib / datetime）、bash（probe 與 E2E 腳本）、pytest + unittest.mock（既有測試風格）、SQLite FTS5（既有索引，不動）。

**前置依賴：** 本批次（PR-F）於 workflow 拓撲中 **await PR-E merge** 後才啟動（spec §6）。PR-E 是 project registry，與本批次無 API 交集——純拓撲序，rebase latest main 後開工即可。分支：`feature/17-cross-cli-consumption`（獨立 git worktree）。

## Global Constraints（自 spec 逐字抄錄，每個 Task 隱含適用）

- **stdlib-only 零新依賴**：所有新程式碼只用 Python 標準函式庫，不得引入任何新第三方套件（spec §3.3「stdlib-only、零新依賴」原則全批次適用）。
- **zh-tw**：「依 repo 來源決定語言——`github.com/hamanpaul/*` → zh-tw。涵蓋 PR 標題／內文與所有 comment。」（policy v1.0.1 語言規範；commit message 同樣 zh-tw）
- **tier: shareable 禁個人絕對路徑**：「`tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。」——凡要 commit 的檔案（docs、測試、腳本、plan）一律用 repo 相對路徑或 `~` / `<tmp>` 佔位。
- **changelog.d 碎片**：「每 code PR：changelog.d 碎片（repo 現行慣例）」（spec §7）。碎片供 release 彙整，本身**不**滿足 R-09——R-09 gate 由 `CHANGELOG.md` `[Unreleased]` bullet 滿足（Task 9 Step 2）。
- **policy_check 零 failure**：「`python3 -m policy_check --repo .` 無任何 failure」（CLAUDE.md 完成任務前 checklist；spec §6 merge gate 亦重跑）。
- **conventional-commit**：「PR title conventional-commit 格式」（R-10）；本 plan 所有 commit message 一律 `type(scope): 描述` zh-tw。
- **R-17**：PR body 引用 issue 必為 closing-keyword 形式（`Closes #17`；`Closes #18` 依 Task 9 條件式判定）。
- **R-18/R-22**：「behavior 變更同步 README／docs 引用（`hippo recall`、…）」（spec §7）——Task 7 落實。
- **R-19**：「測試新增全部進 CI 覆蓋（`tests.yml` 已自動跑 pytest）」——新增 `tests/test_*.py` 自動被收；`tests/*_check.sh` 為手動證據腳本（沿用 `stage2_integration_check.sh` 慣例，不進 CI）。

## 跨批次共享介面契約（本 plan 依約產出，偏離即 bug）

- **契約 5（本批消費）**：CLI 子命令一律走 `cli.py` 的 `memory_subparsers.add_parser` 既有模式；PR-F 新增 `recall（--cwd --prompt --tool --session-id）`。
- **契約 8（本批產出）**：applied 訊號 ledger 事件 schema `{"kind":"applied","session_id","slice_id","tool","ts"}`；CLI 入口 `hippo usage mark-applied`。
- 本 plan 拍板（契約未指定處）：applied 事件 append 至 `runtime/ledger/memory_usage.jsonl`（與 read 事件同檔，以 `kind` 區分；`hippo usage` 已讀該檔，聚合零成本）。
- 本 plan 拍板（契約未指定處；adversarial review 加固）：`mark-applied` 寫入前做**參照完整性驗證（anti-forgery）**——反查 `runtime/ledger/offered.jsonl`，同 `(session_id, tool)` 必須存在先行 offered 記錄、且 `slice_id` 在該些 offer 的 slice 集合內；驗證失敗 → exit 1、不寫入、stderr 說明原因。防任何 shell agent 盲寫假 applied 事件，保 `offered → read → applied` 遙測可信（#18 證據效力）。`hippo recall` 與 prompt-time hook 皆經 `_record_offered` 寫 offered.jsonl，兩條消費路徑的合法 applied 都能通過驗證。

## 任務相依

```
Task 1（capability 實查）──┬─→ Task 5（recall 指引注入；gate：無 prompt-time hook）
                            └─→ Task 6（條件：prompt-time 接線；gate：有 prompt-time hook）
Task 2（hippo recall）→ Task 3（applied 訊號）→ Task 4（per-tool 報表）
Task 2..5 → Task 7（文件漂移）→ Task 8（funnel E2E：hermetic＋live）→ Task 9（收尾/retitle/PR 準備）
```

---

### Task 1: Capability matrix 實查（probe 腳本 + 證據矩陣文件）

**目的**：逐家確認 codex / copilot 的 prompt-time hook、session-start hook、read/tool attribution 能力；以官方文件＋本機實測為據（spec §3.6 行為變更 1）。本 task 不改任何行為程式碼；產出的 verdict 是 Task 5 / Task 6 的 gate。

**Files:**
- Create: `tests/cross_cli_probe_check.sh`
- Create: `docs/cross-cli-capability-matrix.md`

**Interfaces:**
- Consumes: 本機 `codex` / `copilot` CLI（spec §1 已實測可用）；`~/.codex`、`~/.copilot` 既有 auth（僅複製到暫存目錄、退出即刪）。
- Produces: `docs/cross-cli-capability-matrix.md` ——每平台一列 verdict（`supported` / `not-supported` / `inconclusive`），Task 5/6 依此分支；`tests/cross_cli_probe_check.sh` 可重跑 probe。

- [ ] **Step 1: 寫 probe 腳本**

建立 `tests/cross_cli_probe_check.sh`，內容全文：

```bash
#!/usr/bin/env bash
# cross_cli_probe_check.sh — 跨 CLI capability matrix 實查（PR-F Task 1）
#
# 方法：隔離 HOME + marker 檔。對每個平台同時註冊 session-start（對照組）與
# prompt-time（受測組）兩個 hook，各自 touch 一個 marker，跑一輪 headless turn：
#   - prompt marker 出現            → 支援 prompt-time hook（FIRED）
#   - 只有 session-start marker 出現 → harness 有效、prompt-time 不支援（NOT-FIRED）
#   - 兩個都沒出現                  → harness/auth/hook-trust 問題（INCONCLUSIVE，未能實測）
# 手動執行、不進 CI。會把本機 auth 複製到暫存目錄（trap 退出即刪），不落任何持久檔。
set -uo pipefail   # 刻意不用 -e：單一平台失敗仍要跑完並記錄

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_BASE="$ROOT_DIR/.psc_tmp"
mkdir -p "$TMP_BASE"
TMP_DIR="$(mktemp -d "$TMP_BASE/probe-XXXXXX")"
trap 'rm -rf -- "$TMP_DIR"' EXIT

sanitize() { sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" -e "s|$HOME|~|g"; }

verdict() { # $1=標籤 $2=受測 marker $3=對照 marker
  local label="$1" marker="$2" control="$3"
  if [[ -f "$marker" ]]; then
    echo "[probe] ${label}: FIRED（支援）"
  elif [[ -f "$control" ]]; then
    echo "[probe] ${label}: NOT-FIRED（對照組有 fire → 判定不支援）"
  else
    echo "[probe] ${label}: INCONCLUSIVE（對照組也沒 fire → harness/auth/trust 問題，未能實測）"
  fi
}

# ---------------- codex ----------------
if command -v codex >/dev/null 2>&1; then
  echo "=== codex version: $(codex --version 2>&1 | head -1 | sanitize) ==="
  CODEX_HOME_DIR="$TMP_DIR/codex-home"
  mkdir -p "$CODEX_HOME_DIR/.codex"
  cp -a "$HOME/.codex/auth.json" "$CODEX_HOME_DIR/.codex/" 2>/dev/null || true
  cp -a "$HOME/.codex/config.toml" "$CODEX_HOME_DIR/.codex/" 2>/dev/null || true
  M_PROMPT="$TMP_DIR/codex-prompt.marker"; M_START="$TMP_DIR/codex-start.marker"
  cat >"$CODEX_HOME_DIR/.codex/hooks.json" <<EOF
{"hooks": {
  "SessionStart": [{"matcher": "startup|clear|compact", "hooks": [
    {"type": "command", "command": "touch $M_START", "statusMessage": "probe: session-start control"}]}],
  "UserPromptSubmit": [{"matcher": ".*", "hooks": [
    {"type": "command", "command": "touch $M_PROMPT", "statusMessage": "probe: prompt-time"}]}]
}}
EOF
  (cd "$TMP_DIR" && HOME="$CODEX_HOME_DIR" timeout 120 \
    codex exec --skip-git-repo-check "reply with the single word ok" 2>&1 | tail -5 | sanitize) || true
  verdict "codex SessionStart（對照）" "$M_START" "$M_START"
  verdict "codex prompt-time hook"     "$M_PROMPT" "$M_START"
else
  echo "[probe] codex：本機不可用（matrix 標『未實測』）"
fi

# ---------------- copilot ----------------
if command -v copilot >/dev/null 2>&1; then
  echo "=== copilot version: $(copilot --version 2>&1 | head -1 | sanitize) ==="
  COPILOT_HOME_DIR="$TMP_DIR/copilot-home"
  mkdir -p "$COPILOT_HOME_DIR/.copilot/hooks"
  cp -a "$HOME/.copilot/." "$COPILOT_HOME_DIR/.copilot/" 2>/dev/null || true
  rm -f "$COPILOT_HOME_DIR/.copilot/hooks/"*.json 2>/dev/null || true
  M2_PROMPT="$TMP_DIR/copilot-prompt.marker"; M2_START="$TMP_DIR/copilot-start.marker"
  cat >"$COPILOT_HOME_DIR/.copilot/hooks/probe.json" <<EOF
{"version": 1, "hooks": {
  "sessionStart": [{"type": "command", "bash": "touch $M2_START", "timeoutSec": 10}],
  "userPromptSubmit": [{"type": "command", "bash": "touch $M2_PROMPT", "timeoutSec": 10}]
}}
EOF
  (cd "$TMP_DIR" && HOME="$COPILOT_HOME_DIR" timeout 120 \
    copilot -p "reply with the single word ok" 2>&1 | tail -5 | sanitize) || true
  verdict "copilot sessionStart（對照）" "$M2_START" "$M2_START"
  verdict "copilot prompt-time hook"     "$M2_PROMPT" "$M2_START"
else
  echo "[probe] copilot：本機不可用（matrix 標『未實測』）"
fi

echo "[probe] done — 將上列輸出（已去識別）貼入 docs/cross-cli-capability-matrix.md 證據區"
```

- [ ] **Step 2: 設可執行位並跑 probe**

```bash
chmod +x tests/cross_cli_probe_check.sh
bash tests/cross_cli_probe_check.sh
```

預期：每平台各兩行 `[probe] ... FIRED / NOT-FIRED / INCONCLUSIVE` verdict。若對照組 INCONCLUSIVE（常見原因：codex hook 需在互動模式 `/hooks` 信任後才會執行），先於**互動模式**信任後重跑一次；仍 INCONCLUSIVE 就照實記錄，verdict 落「未能實測」。

- [ ] **Step 3: 查官方文件（第二證據源）**

- codex：`codex --help`、`codex exec --help` 全文擷取；查 openai/codex 官方 repo docs 中 hooks 支援的事件清單（記下實際查到的 URL 或文件路徑與 CLI 版本）。
- copilot：`copilot --help`；查 GitHub Copilot CLI 官方文件中 hooks 設定（`~/.copilot/hooks/*.json`）支援的事件 key 清單（同樣記 URL/版本）。
- read/tool attribution 能力（PostToolUse 等價物）：只查文件即可（不強求 fire 實測）；文件無此事件 → 判 `not-supported`。
- 判定規則（寫進矩陣）：**`supported` 需「文件列出該事件」且「本機 probe FIRED」兩者皆備**；只有其一 → `inconclusive`（保守，不接線）。

- [ ] **Step 4: 寫矩陣文件**

建立 `docs/cross-cli-capability-matrix.md`，骨架全文如下；`（填）`欄位以 Step 2/3 的實測輸出與文件引用填入，不得留空：

```markdown
# 跨 CLI 消費能力矩陣（capability matrix）

> 實查日期：（填 YYYY-MM-DD）。probe 腳本：`tests/cross_cli_probe_check.sh`；
> 證據為當日本機輸出（路徑已以 `<tmp>` / `<repo>` / `~` 去識別）。
> 判定規則：`supported` = 官方文件列出該事件 **且** 本機 probe FIRED；僅其一 = `inconclusive`（保守處理，等同不支援，不接線）。

| 能力 | claude-code | codex | copilot-cli |
|---|---|---|---|
| session-start 注入 | supported（SessionStart，既有佈署） | （填 verdict） | （填 verdict） |
| prompt-time shortlist（自動） | supported（UserPromptSubmit，既有佈署） | （填 verdict） | （填 verdict） |
| read attribution | supported（PostToolUse(Read)，既有佈署） | （填 verdict） | （填 verdict） |
| 顯式 recall（`hippo recall`） | supported | supported（session-start 指引注入，PR-F） | supported（session-start 指引注入，PR-F） |
| applied 顯式訊號（`hippo usage mark-applied`） | supported（PR-F，含實證） | 介面可用（無平台注入實證） | 介面可用（無平台注入實證） |
| 總評 | full | （填：recall-capable 或 produce-only） | （填：recall-capable 或 produce-only） |

> 總評語意：`full`＝自動 shortlist＋read attribution 全鏈；`recall-capable`＝無 prompt-time hook，
> 但 agent 可依 session-start 指引顯式呼叫 `hippo recall`；`produce-only`＝連 recall 都不可行
>（該平台 agent 無 shell 工具）。**不假裝 SessionStart orientation 等同 task retrieval。**

## 證據

### codex
- CLI 版本：（填）
- 官方文件依據：（填 URL/文件路徑＋事件清單摘錄）
- probe 輸出（去識別）：

（貼 tests/cross_cli_probe_check.sh 的 codex 段輸出）

### copilot-cli
- CLI 版本：（填）
- 官方文件依據：（填）
- probe 輸出（去識別）：

（貼 copilot 段輸出）

### claude-code（既有佈署，列作基線）
- UserPromptSubmit / PostToolUse(Read) 由 `paulsha_hippo/hooks/install.sh` 佈署（Step 4 / matcher="Read"）。
- 真實 adapter E2E 證據見下方「offered → read → applied 實證」（Task 8 產出時附）。

## offered → read → applied 實證（Task 8 填）

（雙層證據：CI 迴歸保護由 hermetic 全鏈整合測試 `tests/test_cross_cli_funnel_integration.py`
常駐承擔；live 補充證據由 Task 8 執行 `tests/cross_cli_live_check.sh` 後貼入去識別 ledger 輸出：
offered 事件、read 事件（offered=true、同 session_id）、negative control 前後行數、applied 事件。）
```

- [ ] **Step 5: 檢查無個人絕對路徑後 commit**

```bash
grep -rn "/home/" docs/cross-cli-capability-matrix.md tests/cross_cli_probe_check.sh && echo "FAIL: 有個人路徑" || echo OK
git add tests/cross_cli_probe_check.sh docs/cross-cli-capability-matrix.md
git commit -m "docs(cross-cli): 能力矩陣實查——probe 腳本＋官方文件/本機實測證據（#17）"
```

預期：grep 無命中（輸出 `OK`）；commit 成功。

---

### Task 2: `hippo recall` CLI（契約 5 + offered tool attribution）

**Files:**
- Modify: `paulsha_hippo/cli.py:233-239`（usage parser 區塊之後、`return parser` 之前插入 recall parser）；`paulsha_hippo/cli.py:642` 之後新增 `_recall`
- Test: `tests/test_recall_cli.py`（新檔）

**Interfaces:**
- Consumes: `paulsha_hippo.hooks._shortlist_common.build_shortlist_and_record(root: Path, tool: str, session_id: str, cwd: str | None, prompt: str) -> str`（既有，回 shortlist 文字塊或 `''`；內部記 offered ledger + per-session map）
- Produces: `hippo recall --memory-root <path> --cwd <path> --prompt <text> --tool <name> --session-id <id>` → stdout 印 shortlist 塊（無結果印空、皆 exit 0）；副作用：`runtime/ledger/offered.jsonl` append 事件（`tool` 欄位＝`--tool` 值）、`runtime/wakeup/<tool>__<sid>.offered.json` 累積映射

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_recall_cli.py`：

```python
# tests/test_recall_cli.py — hippo recall（跨 CLI consumer API，契約 5）
import json
from pathlib import Path

from paulsha_hippo import cli
from paulsha_hippo.hooks import _shortlist_common as SC
from paulsha_hippo.moc import search as S


def _seed(mr: Path):
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def test_recall_prints_shortlist_and_records_offered_with_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    rc = cli.main(["recall", "--memory-root", str(tmp_path), "--cwd", "/x",
                   "--tool", "codex", "--session-id", "sidR", "--prompt", "SerialWrap 執行"])
    assert rc == 0
    out = capsys.readouterr().out
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    assert note in out and "Read" in out
    events = [json.loads(l) for l in
              (tmp_path / "runtime" / "ledger" / "offered.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(events) == 1
    assert events[0]["tool"] == "codex"           # offered 事件 tool attribution
    assert events[0]["session_id"] == "sidR"
    assert events[0]["offered"] == [{"sl_id": "sl-aaaaaaaaaaaaaaaa", "path": note}]
    m = json.loads((tmp_path / "runtime" / "wakeup" / "codex__sidR.offered.json").read_text())
    assert m["by_id"]["sl-aaaaaaaaaaaaaaaa"] == note


def test_recall_no_match_prints_nothing_exit0(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    rc = cli.main(["recall", "--memory-root", str(tmp_path), "--cwd", "/x",
                   "--tool", "codex", "--session-id", "s", "--prompt", "zzzznomatch"])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_recall_missing_required_flags_exit2(capsys):
    assert cli.main(["recall"]) == 2
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_recall_cli.py -v
```

預期：3 個測試 FAIL——前兩個 `assert 2 == 0`（argparse `invalid choice: 'recall'` → `cli.main` 回 2）；第三個目前也回 2 會意外 PASS 屬正常（invalid choice 同樣 exit 2），以前兩個 FAIL 為紅燈依據。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/cli.py`——在 usage 區塊（現行 233-237 行）之後、`return parser`（現行 239 行）之前插入：

```python
    recall_p = memory_subparsers.add_parser(
        "recall", help="任務相關記憶 shortlist（跨 CLI consumer API；記 offered，含 tool 歸因）")
    recall_p.add_argument("--memory-root", default=str(paths.memory_root()))
    recall_p.add_argument("--cwd", default=None)
    recall_p.add_argument("--prompt", required=True)
    recall_p.add_argument("--tool", required=True)
    recall_p.add_argument("--session-id", required=True)
    recall_p.set_defaults(func=_recall)
```

在 `_memory_usage`（現行 578-642 行）之後新增：

```python
def _recall(args: argparse.Namespace) -> int:
    """跨 CLI consumer API：重用 prompt-time shortlist 管線（best-effort，恆 exit 0）。"""
    from .hooks._shortlist_common import build_shortlist_and_record

    block = build_shortlist_and_record(
        Path(args.memory_root), args.tool, args.session_id, args.cwd, args.prompt)
    if block:
        print(block)
    return 0
```

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_recall_cli.py tests/test_shortlist_common.py tests/test_cli.py -v
```

預期：全 PASS（含既有 shortlist / cli 測試無迴歸）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/cli.py tests/test_recall_cli.py
git commit -m "feat(cli): hippo recall——跨 CLI consumer API（契約 5，offered 含 tool 歸因）（#17）"
```

---

### Task 3: applied 顯式訊號（契約 8：`hippo usage mark-applied` + shortlist 回報指引）

**Files:**
- Modify: `paulsha_hippo/cli.py:233-237`（usage parser 加 optional 子命令）、`paulsha_hippo/cli.py:578-582`（`_memory_usage` 開頭加 guard）、`_recall` 之後新增 `_usage_mark_applied`
- Modify: `paulsha_hippo/hooks/_wakeup_common.py:84-87`（`sanitize_id` 之後新增 `hippo_invocation`）
- Modify: `paulsha_hippo/hooks/_shortlist_common.py:1-15`（imports）、`paulsha_hippo/hooks/_shortlist_common.py:110-148`（新增 `_applied_hint`、`build_shortlist_and_record` 回傳附指引）
- Test: `tests/test_usage_mark_applied.py`（新檔）、`tests/test_shortlist_common.py`（加測試）

**Interfaces:**
- Consumes: 契約 8 schema 定義；`runtime/ledger/offered.jsonl` 事件（`_record_offered` 寫入，`{ts,session_id,tool,project,offered:[{sl_id,path}]}`——參照完整性驗證的反查來源）；`sanitize_id`／`log_warn`（既有）
- Produces:
  - `hippo usage mark-applied --memory-root <path> --session-id <id> --slice-id <sl-id> --tool <name>` → 先做參照完整性驗證（反查 `offered.jsonl`：同 `(session_id, tool)` 有先行 offered 記錄、且 `slice_id` 在該些 offer 的 slice 集合內），通過才 append `{"kind":"applied","session_id":...,"slice_id":...,"tool":...,"ts":...}` 至 `runtime/ledger/memory_usage.jsonl` 並 stdout 回顯該事件 JSON、exit 0；驗證失敗 → 不寫入、stderr 說明原因、exit 1（偽造事件全拒）
  - `paulsha_hippo.hooks._wakeup_common.hippo_invocation(root: Path) -> list[str]`（hooks venv 存在→`[<venv-python>, "-m", "paulsha_hippo"]`，否則 `["python3", "-m", "paulsha_hippo"]`；Task 5 亦消費）
  - shortlist 文字塊尾行含 mark-applied 回報指引（Task 8 的 applied 實證靠它引導 agent）

- [ ] **Step 1: 寫失敗測試（mark-applied CLI）**

建立 `tests/test_usage_mark_applied.py`：

```python
# tests/test_usage_mark_applied.py — applied 顯式訊號（契約 8＋參照完整性 anti-forgery）
import json
from pathlib import Path

from paulsha_hippo import cli


def _seed_offered(mr: Path, session_id: str = "s1", tool: str = "claude-code",
                  slice_ids: tuple[str, ...] = ("sl-aaaaaaaaaaaaaaaa",)):
    """寫一筆 offered 事件（schema 同 _record_offered）——mark-applied 驗證的反查來源。"""
    led = mr / "runtime" / "ledger"
    led.mkdir(parents=True, exist_ok=True)
    ev = {"ts": "2026-07-10T00:00:00Z", "session_id": session_id, "tool": tool,
          "project": "p",
          "offered": [{"sl_id": s, "path": f"/k/{s}.md"} for s in slice_ids]}
    with (led / "offered.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")


def _usage_ledger(mr: Path) -> Path:
    return mr / "runtime" / "ledger" / "memory_usage.jsonl"


def test_mark_applied_appends_contract_event(tmp_path, capsys):
    _seed_offered(tmp_path)  # 先行 offered：s1 / claude-code / sl-aaaaaaaaaaaaaaaa
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s1", "--slice-id", "sl-aaaaaaaaaaaaaaaa",
                   "--tool", "claude-code"])
    assert rc == 0
    lines = _usage_ledger(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["kind"] == "applied"
    assert ev["slice_id"] == "sl-aaaaaaaaaaaaaaaa"
    assert ev["session_id"] == "s1"
    assert ev["tool"] == "claude-code"
    assert ev["ts"]  # ISO timestamp 非空


def test_mark_applied_appends_not_truncates(tmp_path, capsys):
    _seed_offered(tmp_path, session_id="s1", tool="codex", slice_ids=("sl-y",))
    _usage_ledger(tmp_path).write_text(
        json.dumps({"ts": "2026-07-10T00:00:00Z", "session_id": "s0", "tool": "claude-code",
                    "project": "p", "sl_id": "sl-x", "path": "/k/x.md",
                    "source": "read", "offered": True}) + "\n", encoding="utf-8")
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s1", "--slice-id", "sl-y", "--tool", "codex"])
    assert rc == 0
    lines = _usage_ledger(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["source"] == "read"


# ---- 參照完整性 negative cases：偽造 applied 一律拒絕（exit 1、不寫入、stderr 說明）----

def test_mark_applied_rejects_unknown_session(tmp_path, capsys):
    _seed_offered(tmp_path, session_id="s1")
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s-forged", "--slice-id", "sl-aaaaaaaaaaaaaaaa",
                   "--tool", "claude-code"])
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()  # 偽造事件未落 ledger


def test_mark_applied_rejects_unknown_slice(tmp_path, capsys):
    _seed_offered(tmp_path, slice_ids=("sl-aaaaaaaaaaaaaaaa",))
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s1", "--slice-id", "sl-neveroffered0001",
                   "--tool", "claude-code"])
    assert rc == 1
    assert "slice_id" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_mark_applied_rejects_tool_mismatch(tmp_path, capsys):
    _seed_offered(tmp_path, tool="claude-code")
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s1", "--slice-id", "sl-aaaaaaaaaaaaaaaa",
                   "--tool", "codex"])  # 同 session/slice、tool 不符 → 拒
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_mark_applied_rejects_when_no_offered_ledger(tmp_path, capsys):
    # 全新 memory root、無任何 offer——「任何 shell agent 盲寫假事件」的原始攻擊面
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "s1", "--slice-id", "sl-aaaaaaaaaaaaaaaa",
                   "--tool", "claude-code"])
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_usage_without_memory_root_errors_exit2(tmp_path, capsys):
    assert cli.main(["usage"]) == 2
    assert "memory-root" in capsys.readouterr().err


def test_usage_report_still_works_without_subcommand(tmp_path, capsys):
    assert cli.main(["usage", "--memory-root", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["summary"]["sessions"] == 0
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_usage_mark_applied.py -v
```

預期：`test_mark_applied_*` 六個全 FAIL——兩個 append 測試 `assert 2 == 0`、四個 negative cases `assert 2 == 1`（現況 argparse `unrecognized arguments: mark-applied ...` 一律回 2）；`test_usage_without_memory_root_errors_exit2` 目前 PASS（argparse required 也回 2）；`test_usage_report_still_works_without_subcommand` PASS。紅燈依據為 mark-applied 六個。

- [ ] **Step 3: 實作 CLI**

`paulsha_hippo/cli.py`——把現行 233-237 行的 usage 區塊：

```python
    usage_p = memory_subparsers.add_parser("usage")
    usage_p.add_argument("--memory-root", required=True)
    usage_p.add_argument("--since", default=None)
    usage_p.add_argument("--json", action="store_true")
    usage_p.set_defaults(func=_memory_usage)
```

整段替換為：

```python
    usage_p = memory_subparsers.add_parser("usage")
    # --memory-root 改 optional（_memory_usage 開頭手動檢查、缺值仍 exit 2）：
    # 讓契約形式 `hippo usage mark-applied --memory-root ...` 可被 argparse 解析
    #（parent required optional 會在子命令 token 前就報錯）。
    usage_p.add_argument("--memory-root", default=None)
    usage_p.add_argument("--since", default=None)
    usage_p.add_argument("--json", action="store_true")
    usage_p.set_defaults(func=_memory_usage)
    usage_sub = usage_p.add_subparsers(dest="usage_command")
    mark_applied_p = usage_sub.add_parser(
        "mark-applied", help="記錄 applied 顯式訊號（agent structured acknowledgement，契約 8）")
    mark_applied_p.add_argument("--memory-root", required=True)
    mark_applied_p.add_argument("--session-id", required=True)
    mark_applied_p.add_argument("--slice-id", required=True)
    mark_applied_p.add_argument("--tool", required=True)
    mark_applied_p.set_defaults(func=_usage_mark_applied)
```

`_memory_usage`（現行 578 行起）函式開頭、`root = Path(args.memory_root)` 之前插入：

```python
    if not args.memory_root:
        print("hippo usage: error: --memory-root is required", file=sys.stderr)
        return 2
```

`_recall` 之後新增：

```python
def _usage_mark_applied(args: argparse.Namespace) -> int:
    """applied 顯式訊號（契約 8）：agent 主動回報某條記憶實際影響了做法。

    參照完整性（anti-forgery）：寫入前反查 offered.jsonl——同 (session_id, tool)
    必須存在先行 offered 記錄、且 slice_id 在該些 offer 的 slice 集合內；
    否則 exit 1、不寫入、stderr 說明原因。applied 只能指向真的被 offer 過的記憶，
    不然 offered→read→applied 漏斗可被盲寫偽造、遙測不可信（#18 證據失效）。
    """
    led_dir = Path(args.memory_root) / "runtime" / "ledger"
    session_seen = False
    offered_slices: set[str] = set()
    offered_path = led_dir / "offered.jsonl"
    if offered_path.exists():
        for line in offered_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("session_id") != args.session_id or e.get("tool") != args.tool:
                continue
            session_seen = True
            for o in e.get("offered", []):
                sid = o.get("sl_id") if isinstance(o, dict) else o
                if sid:
                    offered_slices.add(str(sid))
    if not session_seen:
        print(f"hippo usage mark-applied: error: 查無 (session_id={args.session_id}, "
              f"tool={args.tool}) 的先行 offered 記錄——拒絕寫入"
              "（applied 只能回報真實被 offer 的記憶）", file=sys.stderr)
        return 1
    if args.slice_id not in offered_slices:
        print(f"hippo usage mark-applied: error: slice_id={args.slice_id} 不在 "
              f"(session_id={args.session_id}, tool={args.tool}) 的 offered slice 集合內"
              "——拒絕寫入", file=sys.stderr)
        return 1
    ev = {"kind": "applied",
          "session_id": args.session_id,
          "slice_id": args.slice_id,
          "tool": args.tool,
          "ts": datetime.now(timezone.utc).isoformat()}
    led_dir.mkdir(parents=True, exist_ok=True)
    with (led_dir / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(json.dumps(ev, ensure_ascii=False))
    return 0
```

（`datetime` / `timezone` 已於 cli.py 第 8 行 import；`json` / `sys` 亦於檔頭 import，直接可用。）

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_usage_mark_applied.py tests/test_memory_usage_cli.py -v
```

預期：全 PASS（含四個偽造拒絕 negative cases；既有 usage 報表測試無迴歸）。

- [ ] **Step 5: 寫失敗測試（shortlist 回報指引）**

`tests/test_shortlist_common.py` 檔尾新增：

```python
def test_shortlist_appends_applied_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidA",
                                        cwd="/x", prompt="SerialWrap 執行")
    assert "usage mark-applied" in out
    assert "--session-id sidA" in out and "--tool claude-code" in out
    assert f"--memory-root {tmp_path}" in out


def test_shortlist_empty_result_has_no_applied_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "s",
                                        cwd="/x", prompt="zzzznomatch")
    assert out == ""
```

- [ ] **Step 6: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_shortlist_common.py::test_shortlist_appends_applied_hint -v
```

預期：FAIL `AssertionError`（`"usage mark-applied" in out` 不成立）。

- [ ] **Step 7: 實作 helper 與指引**

`paulsha_hippo/hooks/_wakeup_common.py`——在 `sanitize_id`（現行 84-86 行）之後新增：

```python
def hippo_invocation(root: Path) -> list[str]:
    """可在該部署呼叫 hippo CLI 的 argv 前綴。

    hooks venv 存在（正式安裝）→ 用其 python -m；否則退 python3 -m（開發/測試環境，
    需 caller 環境可 import paulsha_hippo）。不用 console script `hippo`：wheel 複製
    安裝情境（install.sh Step 2 fallback）venv 內沒有 bin/hippo。
    """
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-m", "paulsha_hippo"]
    return ["python3", "-m", "paulsha_hippo"]
```

`paulsha_hippo/hooks/_shortlist_common.py`——imports 修改：第 5 行 `import re` 之後加一行 `import shlex`（維持字母序）；第 12 行改為：

```python
from paulsha_hippo.hooks._wakeup_common import hippo_invocation, log_warn, sanitize_id
```

在 `build_shortlist_and_record`（現行 112 行）之前新增：

```python
def _applied_hint(root: Path, tool: str, session_id: str) -> str:
    """applied 顯式訊號回報指引（契約 8）：附完整可貼命令（session 歸因已填）。"""
    argv = hippo_invocation(root) + [
        "usage", "mark-applied", "--memory-root", str(root),
        "--session-id", session_id, "--tool", tool, "--slice-id"]
    cmd = " ".join(shlex.quote(a) for a in argv)
    return (f"> 若上列某條記憶實際影響了你的做法，回報 applied（--slice-id 值＝"
            f"該筆記 frontmatter 的 slice_id）：`{cmd} <slice_id>`")
```

`build_shortlist_and_record` 內（現行 143-145 行）：

```python
        offered = [(h["slice_id"], h["path"]) for h in hits if h.get("path")]
        _record_offered(root, tool, session_id, project, offered)
        return block
```

改為：

```python
        offered = [(h["slice_id"], h["path"]) for h in hits if h.get("path")]
        _record_offered(root, tool, session_id, project, offered)
        return block + "\n" + _applied_hint(root, tool, session_id)
```

- [ ] **Step 8: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_shortlist_common.py tests/test_user_prompt_submit_hook.py -v
```

預期：全 PASS（含既有 fail-closed / dedup 測試——空結果與 redaction 失敗路徑在附加指引之前就 return `""`，不受影響）。

- [ ] **Step 9: Commit**

```bash
git add paulsha_hippo/cli.py paulsha_hippo/hooks/_wakeup_common.py \
        paulsha_hippo/hooks/_shortlist_common.py \
        tests/test_usage_mark_applied.py tests/test_shortlist_common.py
git commit -m "feat(usage): applied 顯式訊號——hippo usage mark-applied＋shortlist 回報指引（契約 8）（#18）"
```

---

### Task 4: usage 報表 per-tool 分列（offered / read / applied，無訊號 n/a）

**Files:**
- Modify: `paulsha_hippo/cli.py:578-642`（`_memory_usage` 聚合與輸出）
- Test: `tests/test_memory_usage_cli.py`（加測試）

**Interfaces:**
- Consumes: `offered.jsonl` 事件（含 `tool`、`offered:[{sl_id,path}]`）；`memory_usage.jsonl` 的 read 事件（`source:"read"`、含 `tool`）與 applied 事件（`kind:"applied"`、含 `tool`，Task 3 產出）
- Produces: `hippo usage --json` 報表新增頂層 `"by_tool": {tool: {"offered": int, "read": int, "applied": int | null}}`（`null`＝該 tool 無任何 applied 訊號＝文字模式顯示 `n/a`；**不做內容 substring 猜測**）；文字模式新增 `  tool=<name> offered=N read=N applied=N|n/a` 行

- [ ] **Step 1: 寫失敗測試**

`tests/test_memory_usage_cli.py` 檔尾新增：

```python
def test_memory_usage_by_tool_with_applied_and_na(tmp_path, capsys):
    led = tmp_path / "runtime" / "ledger"
    led.mkdir(parents=True)
    (led / "offered.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:00:00Z", "session_id": "s1", "tool": "claude-code",
                    "project": "p", "offered": [{"sl_id": "sl-a", "path": "/k/a.md"}]}) + "\n" +
        json.dumps({"ts": "2026-07-10T01:01:00Z", "session_id": "s2", "tool": "codex",
                    "project": "p", "offered": [{"sl_id": "sl-b", "path": "/k/b.md"},
                                                {"sl_id": "sl-c", "path": "/k/c.md"}]}) + "\n",
        encoding="utf-8")
    (led / "memory_usage.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:05:00Z", "session_id": "s1", "tool": "claude-code",
                    "project": "p", "sl_id": "sl-a", "path": "/k/a.md",
                    "source": "read", "offered": True}) + "\n" +
        json.dumps({"kind": "applied", "session_id": "s1", "slice_id": "sl-a",
                    "tool": "claude-code", "ts": "2026-07-10T01:06:00Z"}) + "\n",
        encoding="utf-8")
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=True)
    assert _memory_usage(args) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["by_tool"]["claude-code"] == {"offered": 1, "read": 1, "applied": 1}
    # codex：offered 2、read 0、applied 無訊號 → null（n/a），不猜測補值
    assert rep["by_tool"]["codex"] == {"offered": 2, "read": 0, "applied": None}
    # applied 事件不得污染 read 聚合
    assert rep["summary"]["total_reads"] == 1


def test_memory_usage_text_mode_renders_applied_na(tmp_path, capsys):
    led = tmp_path / "runtime" / "ledger"
    led.mkdir(parents=True)
    (led / "offered.jsonl").write_text(
        json.dumps({"ts": "2026-07-10T01:00:00Z", "session_id": "s2", "tool": "codex",
                    "project": "p", "offered": [{"sl_id": "sl-b", "path": "/k/b.md"}]}) + "\n",
        encoding="utf-8")
    args = argparse.Namespace(memory_root=str(tmp_path), since=None, json=False)
    assert _memory_usage(args) == 0
    out = capsys.readouterr().out
    assert "tool=codex offered=1 read=0 applied=n/a" in out
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_memory_usage_cli.py -v
```

預期：新增兩測試 FAIL（`KeyError: 'by_tool'` 與 `AssertionError`）；既有兩測試 PASS。

- [ ] **Step 3: 實作**

`paulsha_hippo/cli.py` 的 `_memory_usage` 整函式替換為（含 Task 3 已加的 guard；行號基準：原 578-642）：

```python
def _memory_usage(args: argparse.Namespace) -> int:
    from collections import defaultdict

    if not args.memory_root:
        print("hippo usage: error: --memory-root is required", file=sys.stderr)
        return 2

    root = Path(args.memory_root)
    led = root / "runtime" / "ledger"

    def _read_jsonl(p):
        out = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if args.since and str(e.get("ts", "")) < args.since:
                    continue
                out.append(e)
        return out

    offered_rows = _read_jsonl(led / "offered.jsonl")
    usage_rows = _read_jsonl(led / "memory_usage.jsonl")
    used_rows = [e for e in usage_rows if e.get("source") == "read"]
    applied_rows = [e for e in usage_rows if e.get("kind") == "applied"]

    agg = defaultdict(lambda: {"offered_count": 0, "read_count": 0, "last_read": ""})
    sessions = set()
    for e in offered_rows:
        sessions.add(e.get("session_id"))
        for o in e.get("offered", []):
            sid = o.get("sl_id") if isinstance(o, dict) else o
            if sid:
                agg[sid]["offered_count"] += 1
    for e in used_rows:
        # Count the session even when the read was not from an offered/attributable
        # slice, so avg_reads_per_session is not skewed by offered-only session counting.
        sessions.add(e.get("session_id"))
        sid = e.get("sl_id") or "(unattributed)"
        ts = str(e.get("ts", ""))
        agg[sid]["read_count"] += 1
        if ts > agg[sid]["last_read"]:
            agg[sid]["last_read"] = ts

    def _tool_key(e) -> str:
        return str(e.get("tool") or "(unknown)")

    by_tool: dict[str, dict] = {}
    for e in offered_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["offered"] += len(e.get("offered", []))
    for e in used_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["read"] += 1
    applied_tools: set[str] = set()
    for e in applied_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["applied"] += 1
        applied_tools.add(_tool_key(e))
    for name, t in by_tool.items():
        if name not in applied_tools:
            t["applied"] = None  # 該 tool 無任何 applied 訊號 → n/a（不以內容猜測補值）

    slices = [{"slice_id": sid, **v} for sid, v in agg.items()]
    slices.sort(key=lambda s: (s["read_count"], s["offered_count"]), reverse=True)
    never_read = sum(1 for s in slices if s["offered_count"] > 0 and s["read_count"] == 0)
    n = len(sessions)
    total_reads = len(used_rows)
    summary = {
        "sessions": n, "slices": len(slices), "never_read": never_read,
        "total_reads": total_reads,
        "avg_reads_per_session": round(total_reads / n, 3) if n else 0.0,
    }
    report = {"summary": summary,
              "by_tool": {k: by_tool[k] for k in sorted(by_tool)},
              "slices": slices}

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"sessions={summary['sessions']} slices={summary['slices']} "
              f"never_read={summary['never_read']} total_reads={summary['total_reads']} "
              f"avg_reads/session={summary['avg_reads_per_session']}")
        for name in sorted(by_tool):
            t = by_tool[name]
            applied_disp = "n/a" if t["applied"] is None else str(t["applied"])
            print(f"  tool={name} offered={t['offered']} read={t['read']} applied={applied_disp}")
        for s in slices[:30]:
            print(f"  {s['slice_id']}  offered={s['offered_count']} "
                  f"read={s['read_count']} last_read={s['last_read']}")
    return 0
```

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_memory_usage_cli.py tests/test_usage_mark_applied.py -v
```

預期：全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/cli.py tests/test_memory_usage_cli.py
git commit -m "feat(usage): 報表 per-tool offered/read/applied 分列——無 applied 訊號顯示 n/a（#18）"
```

---

### Task 5: codex / copilot SessionStart 注入顯式 recall 指引

**Gate（依 Task 1 matrix verdict，逐平台判定）**：本 task 的「翻旗」步驟（Step 5）只對 **prompt-time hook verdict ≠ supported** 的平台執行（`not-supported` 與 `inconclusive` 都算「沒有」——保守）。依現況預期 codex、copilot 皆走本路徑；若某平台 verdict=supported，該平台跳過 Step 5 的翻旗（走 Task 6 接線），其餘步驟（機制與測試）照做。

**Files:**
- Modify: `paulsha_hippo/wakeup/builder.py:320-334`（`build_orientation` 加 `retrieval_hint` 參數）
- Modify: `paulsha_hippo/hooks/_wakeup_common.py:143-158`（`compute_brief_and_record` 加 `recall_guidance` 參數）與同檔新增 `format_recall_command` / `recall_guidance_hint`
- Modify: `paulsha_hippo/hooks/codex_session_start.py:36`、`paulsha_hippo/hooks/copilot_session_start.py:38`（傳 `recall_guidance=True`）
- Test: `tests/test_recall_guidance.py`（新檔）、`tests/test_wakeup_builder.py`（加測試）

**Interfaces:**
- Consumes: `hippo_invocation(root: Path) -> list[str]`（Task 3 產出）；`hippo recall` CLI（Task 2 產出，指引文字引用它）
- Produces:
  - `paulsha_hippo.wakeup.builder.build_orientation(memory_root, project: str, *, retrieval_hint: str | None = None) -> str`（`None`→既有預設句，向後相容）
  - `paulsha_hippo.hooks._wakeup_common.format_recall_command(root: Path, tool: str, session_id: str, cwd: str | None) -> str`
  - `paulsha_hippo.hooks._wakeup_common.recall_guidance_hint(root: Path, tool: str, session_id: str, cwd: str | None) -> str`
  - `paulsha_hippo.hooks._wakeup_common.compute_brief_and_record(root: Path, tool: str, session_id: str, cwd: str | None, *, recall_guidance: bool = False) -> str`

- [ ] **Step 1: 寫失敗測試（builder 參數）**

`tests/test_wakeup_builder.py` 檔尾新增：

```python
def test_build_orientation_custom_retrieval_hint(tmp_path):
    from paulsha_hippo.wakeup.builder import build_orientation
    k = tmp_path / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text("---\nmemory_layer: knowledge\n---\nx\n", encoding="utf-8")
    out = build_orientation(tmp_path, "proj", retrieval_hint="CUSTOM-HINT")
    assert "CUSTOM-HINT" in out
    assert "每次 prompt 後以短清單浮現" not in out
    default = build_orientation(tmp_path, "proj")
    assert "每次 prompt 後以短清單浮現" in default
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_wakeup_builder.py::test_build_orientation_custom_retrieval_hint -v
```

預期：FAIL `TypeError: build_orientation() got an unexpected keyword argument 'retrieval_hint'`。

- [ ] **Step 3: 實作 builder 參數**

`paulsha_hippo/wakeup/builder.py`——把現行 320-334 行的 `build_orientation` 整函式替換為：

```python
_ORIENTATION_RETRIEVAL_HINT = ("與當前任務相關的記憶會在每次 prompt 後以短清單浮現；"
                               "用 Read 開啟清單中列出的絕對路徑即取全文。")


def build_orientation(memory_root, project: str, *, retrieval_hint: str | None = None) -> str:
    """Concise SessionStart orientation (no MOC dump). '' when project has no notes.

    retrieval_hint：檢索方式說明句。None → 預設「prompt 後自動浮現」（Claude 的
    prompt-time hook 行為）；無 prompt-time hook 的平台傳入顯式 recall 指引，
    不假裝 orientation 等同 task retrieval。
    """
    from pathlib import Path as _Path
    from ..atomizer.config import sanitize_project_component
    safe = sanitize_project_component(project)
    pdir = _Path(memory_root) / "knowledge" / safe
    n = 0
    if pdir.exists():
        # rglob for parity with build_index's knowledge walk (count is approximate, "約").
        n = sum(1 for p in pdir.rglob("*.md") if not p.name.endswith("-moc.md"))
    if n == 0:
        return ""
    hint = retrieval_hint if retrieval_hint is not None else _ORIENTATION_RETRIEVAL_HINT
    return (f"# 記憶 — {project}\n\n"
            f"記憶系統已啟用（本專案約 {n} 筆 knowledge）。{hint}")
```

跑 `python3 -m pytest tests/test_wakeup_builder.py -v`——預期全 PASS。

- [ ] **Step 4: 寫失敗測試（hooks 注入）**

建立 `tests/test_recall_guidance.py`：

```python
"""codex/copilot SessionStart 注入顯式 recall 指引（無 prompt-time hook 平台，#17）。

沿用 test_session_start_wiring 的 mock 手法：mock resolve_project、真跑
compute_brief_and_record + build_orientation（需 seed 一筆 knowledge 使 n>0）。
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def _seed_knowledge(root: Path):
    k = root / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: T\ncaptured_at: '2026-07-10T00:00:00Z'\n---\nbody\n", encoding="utf-8")


class RecallGuidanceTests(unittest.TestCase):
    def _ctx(self, module_name: str) -> str:
        import importlib
        mod = importlib.import_module(module_name)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_knowledge(root)
            payload = {"session_id": "sidG", "cwd": "/x"}
            out = io.StringIO()
            with mock.patch.dict("os.environ", {"PSC_MEMORY_ROOT": str(root)}), \
                 mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 mock.patch("paulsha_hippo.importer.project_resolver.resolve_project",
                            return_value="proj"), \
                 mock.patch("sys.stdout", out):
                mod.main()
            data = json.loads(out.getvalue())
            if "hookSpecificOutput" in data:
                return data["hookSpecificOutput"]["additionalContext"]
            return data["additionalContext"]

    def test_codex_session_start_injects_recall_guidance(self):
        ctx = self._ctx("paulsha_hippo.hooks.codex_session_start")
        self.assertIn("recall", ctx)
        self.assertIn("--tool codex", ctx)
        self.assertIn("--session-id sidG", ctx)
        self.assertNotIn("每次 prompt 後以短清單浮現", ctx)

    def test_copilot_session_start_injects_recall_guidance(self):
        ctx = self._ctx("paulsha_hippo.hooks.copilot_session_start")
        self.assertIn("recall", ctx)
        self.assertIn("--tool copilot-cli", ctx)
        self.assertNotIn("每次 prompt 後以短清單浮現", ctx)

    def test_claude_session_start_keeps_auto_shortlist_hint(self):
        ctx = self._ctx("paulsha_hippo.hooks.claude_session_start")
        self.assertIn("每次 prompt 後以短清單浮現", ctx)
        self.assertNotIn("mark-applied", ctx)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_recall_guidance.py -v
```

預期：codex / copilot 兩測試 FAIL（`AssertionError`——目前注入的是「每次 prompt 後以短清單浮現」預設句、無 recall 指令）；claude 測試 PASS。

- [ ] **Step 6: 實作 hint 組裝與 hooks 翻旗**

`paulsha_hippo/hooks/_wakeup_common.py`——在 `hippo_invocation`（Task 3 加入）之後新增：

```python
def format_recall_command(root: Path, tool: str, session_id: str, cwd: str | None) -> str:
    """組出顯式 recall 指令字串（tool/session-id 歸因已填、--prompt 留說明佔位）。"""
    import shlex
    argv = hippo_invocation(root) + ["recall", "--memory-root", str(root),
                                     "--tool", tool, "--session-id", session_id]
    if cwd:
        argv += ["--cwd", str(cwd)]
    return " ".join(shlex.quote(a) for a in argv) + ' --prompt "<當前任務描述>"'


def recall_guidance_hint(root: Path, tool: str, session_id: str, cwd: str | None) -> str:
    """無 prompt-time hook 平台的顯式 recall 指引（capability matrix: recall-capable）。"""
    return ("本平台不會在每次 prompt 自動浮現任務相關記憶；需要任務相關記憶時，執行：\n"
            f"`{format_recall_command(root, tool, session_id, cwd)}`\n"
            "再用 Read 開啟輸出清單中的絕對路徑取全文。")
```

同檔 `compute_brief_and_record`（現行 143-158 行）整函式替換為：

```python
def compute_brief_and_record(root: Path, tool: str, session_id: str, cwd: str | None,
                             *, recall_guidance: bool = False) -> str:
    """SessionStart 極簡 orientation；不再前置引用前言、不再寫 session-wide offered。

    recall_guidance=True：無 prompt-time hook 的平台改注入顯式 recall 指引
    （不假裝 SessionStart orientation 等同 task retrieval）。
    """
    try:
        from paulsha_hippo.importer.project_resolver import resolve_project
        from paulsha_hippo.wakeup.builder import build_orientation
    except ImportError as exc:
        log_warn(root, tool, f"failed to import resolver or builder: {exc}")
        return ""
    try:
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""
        if not recall_guidance:
            return build_orientation(root, project)
        return build_orientation(
            root, project,
            retrieval_hint=recall_guidance_hint(root, tool, session_id, cwd))
    except Exception as exc:
        log_warn(root, tool, f"failed to build orientation: {exc}")
        return ""
```

**逐平台翻旗（gate 適用處）**：

`paulsha_hippo/hooks/codex_session_start.py` 第 36 行：

```python
        brief = compute_brief_and_record(root, TOOL, session_id, payload.get("cwd"))
```

改為：

```python
        # capability matrix（docs/cross-cli-capability-matrix.md）：codex 無 prompt-time
        # hook → 注入顯式 recall 指引。若日後實測轉為 supported，改回 False 並走接線。
        brief = compute_brief_and_record(root, TOOL, session_id, payload.get("cwd"),
                                         recall_guidance=True)
```

`paulsha_hippo/hooks/copilot_session_start.py` 第 38 行：

```python
        brief = compute_brief_and_record(root, TOOL, session_id, cwd)
```

改為：

```python
        # capability matrix：copilot 無 prompt-time hook → 注入顯式 recall 指引。
        brief = compute_brief_and_record(root, TOOL, session_id, cwd,
                                         recall_guidance=True)
```

（若某平台 matrix verdict=supported：該平台不改此行，對應測試（Step 4 該平台的 case）改為斷言預設句仍在，並在 Task 6 接線。）

- [ ] **Step 7: 跑測試確認 PASS（含迴歸）**

```bash
python3 -m pytest tests/test_recall_guidance.py tests/test_session_start_wiring.py \
    tests/test_session_start_hooks.py tests/test_wakeup_builder.py -v
```

預期：全 PASS。（`test_session_start_hooks` 斷言「記憶系統已啟用」與「Read」——指引句含「再用 Read 開啟…」，兩者皆保住。）

- [ ] **Step 8: Commit**

```bash
git add paulsha_hippo/wakeup/builder.py paulsha_hippo/hooks/_wakeup_common.py \
        paulsha_hippo/hooks/codex_session_start.py paulsha_hippo/hooks/copilot_session_start.py \
        tests/test_recall_guidance.py tests/test_wakeup_builder.py
git commit -m "feat(hooks): codex/copilot SessionStart 注入顯式 recall 指引——不假裝 orientation 等同 task retrieval（#17）"
```

---

### Task 6:（條件）prompt-time adapter 接線

**Gate（依 Task 1 matrix verdict，逐平台判定）**：**只對 verdict=supported 的平台執行**；預期結果是兩平台皆 not-supported → 本 task 整個跳過，於 PR body 記一行「Task 6 skipped：capability matrix 判定 codex/copilot 皆無 prompt-time hook（見 docs/cross-cli-capability-matrix.md）」。以下 Step 1-5 為 codex 分支；Step 6 為 copilot 分支（完整代碼，僅該平台 verdict=supported 時執行）。

**Files:**
- Create: `paulsha_hippo/hooks/codex_user_prompt_submit.py`（copilot 情境：`paulsha_hippo/hooks/copilot_user_prompt_submit.py`）
- Modify: `paulsha_hippo/hooks/install.sh:181-185`（deploy 清單）、`paulsha_hippo/hooks/install.sh:337-449`（codex 段：命令變數、heredoc argv、reconcile 呼叫）
- Modify: `paulsha_hippo/hooks/uninstall.sh:203-208`（codex managed 事件移除清單）
- Modify: `paulsha_hippo/hooks/codex_session_start.py`（該平台翻旗回 `recall_guidance=False`，即還原 Task 5 Step 6 該平台的修改）
- Test: `tests/test_hooks.py`（deploy 清單斷言，現行 397-411 行區域；wiring 斷言仿現行 473-491 行 UserPromptSubmit 模式）

**Interfaces:**
- Consumes: `build_shortlist_and_record(root, tool, session_id, cwd, prompt) -> str`（既有）；Task 1 matrix 的實測事件名與 payload schema
- Produces: `<memory_root>/hooks/codex_user_prompt_submit.py` 佈署檔＋`~/.codex/hooks.json` 內 managed prompt-time entry

- [ ] **Step 1: 寫失敗測試（hook 腳本行為）**

建立 `tests/test_codex_user_prompt_submit_hook.py`：

```python
# tests/test_codex_user_prompt_submit_hook.py —（條件 task：matrix verdict=supported 才存在）
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/codex_user_prompt_submit.py").resolve()


def _seed(mr: Path):
    from paulsha_hippo.moc import search as S
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def _run(mr: Path, payload: dict) -> dict:
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def test_codex_prompt_hook_injects_and_attributes_tool(tmp_path):
    _seed(tmp_path)
    proj_cwd = tmp_path / "proj"; proj_cwd.mkdir(exist_ok=True)
    out = _run(tmp_path, {"session_id": "cx1", "cwd": str(proj_cwd),
                          "prompt": "SerialWrap 執行"})
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "a.md" in ctx and "Read" in ctx
    led = (tmp_path / "runtime" / "ledger" / "offered.jsonl").read_text(encoding="utf-8")
    ev = json.loads(led.splitlines()[0])
    assert ev["tool"] == "codex" and ev["session_id"] == "cx1"


def test_codex_prompt_hook_error_emits_empty_exit0(tmp_path):
    out = _run(tmp_path, {"session_id": "cx2", "cwd": "/nonexistent", "prompt": "x"})
    assert out.get("hookSpecificOutput", {}).get("additionalContext", "") == ""
```

跑 `python3 -m pytest tests/test_codex_user_prompt_submit_hook.py -v`——預期：FAIL（`FileNotFoundError`：HOOK 檔不存在）。

- [ ] **Step 2: 實作 hook 腳本**

建立 `paulsha_hippo/hooks/codex_user_prompt_submit.py`（`EVENT_NAME` 以 Task 1 matrix 實測事件名為準；下例為 Claude-mirroring 名）：

```python
#!/usr/bin/env python3
"""Codex prompt-time hook: inject task-relevant memory shortlist.

僅在 capability matrix（docs/cross-cli-capability-matrix.md）判定 codex
prompt-time hook = supported 時佈署。Any error -> empty context, exit 0.
"""
from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "codex"
EVENT_NAME = "UserPromptSubmit"  # ← 以 matrix 實測事件名為準


def main() -> int:
    from paulsha_hippo.hooks._shortlist_common import build_shortlist_and_record
    from paulsha_hippo.hooks._wakeup_common import log_warn, memory_root, read_payload

    root = memory_root()
    payload = read_payload(root, TOOL)
    context = ""
    try:
        cwd = payload.get("cwd")
        session_id = str(payload.get("session_id") or "unknown")
        prompt = str(payload.get("prompt") or "")
        context = build_shortlist_and_record(root, TOOL, session_id, cwd, prompt)
    except Exception as exc:
        log_warn(root, TOOL, f"user_prompt_submit failed: {exc}")
        context = ""

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": EVENT_NAME, "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

跑 `python3 -m pytest tests/test_codex_user_prompt_submit_hook.py -v`——預期全 PASS。

- [ ] **Step 3: install.sh / uninstall.sh 佈線**

`paulsha_hippo/hooks/install.sh`：

(a) deploy 清單（現行 181-185 行）在 `claude_user_prompt_submit.py claude_post_tool_use.py _shortlist_common.py` 後補 `codex_user_prompt_submit.py`：

```bash
for script in install.sh uninstall.sh \
  claude_session_end.py codex_session_end.py copilot_session_end.py \
  _wakeup_common.py _bootstrap.py claude_session_start.py codex_session_start.py \
  copilot_session_start.py claude_precompact.py copilot_precompact.py \
  claude_user_prompt_submit.py claude_post_tool_use.py _shortlist_common.py \
  codex_user_prompt_submit.py; do
```

(b) codex 段（現行 337-339 行）加命令變數：

```bash
codex_user_prompt_submit_command="${hook_env_prefix} ${venv_python} ${hook_dir}/codex_user_prompt_submit.py"
```

(c) heredoc 呼叫（現行 347-348 行）尾端多傳一個參數 `"$codex_user_prompt_submit_command"`，heredoc 內（現行 351-356 行）加 `user_prompt_submit_command = sys.argv[6]`，並在 `_reconcile_event("SessionStart", ...)`（現行 441-444 行）之後加：

```python
_reconcile_event(
    "UserPromptSubmit", user_prompt_submit_command, "paulsha-memory: injecting task shortlist",
    "codex_user_prompt_submit.py", matcher=".*",
)
```

`paulsha_hippo/hooks/uninstall.sh`（現行 203-208 行之後）加：

```python
if "UserPromptSubmit" in hooks:
    hooks["UserPromptSubmit"] = _remove_managed(hooks["UserPromptSubmit"], "codex_user_prompt_submit.py")
```

(d) `tests/test_hooks.py` 的 deploy 清單斷言（現行 397-411 行區域）補 `"codex_user_prompt_submit.py"`；並仿現行 473-491 行 UserPromptSubmit reconcile 測試，為 codex hooks.json 加對應 wiring 斷言。

(e) 該平台翻旗還原：`codex_session_start.py` 的 `recall_guidance=True` 改回無此參數（自動 shortlist 已接線，不需指引）；`tests/test_recall_guidance.py` 對應 case 改斷言預設句。

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_hooks.py tests/test_codex_user_prompt_submit_hook.py tests/test_recall_guidance.py -v
```

預期：全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/hooks/codex_user_prompt_submit.py paulsha_hippo/hooks/install.sh \
        paulsha_hippo/hooks/uninstall.sh paulsha_hippo/hooks/codex_session_start.py \
        tests/test_hooks.py tests/test_codex_user_prompt_submit_hook.py tests/test_recall_guidance.py
git commit -m "feat(hooks): codex prompt-time shortlist 接線（capability matrix 實測 supported）（#17）"
```

- [ ] **Step 6:（copilot verdict=supported 時）copilot 分支——完整對稱實作**

(a) 失敗測試 `tests/test_copilot_user_prompt_submit_hook.py`：

```python
# tests/test_copilot_user_prompt_submit_hook.py —（條件 task：matrix verdict=supported 才存在）
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/copilot_user_prompt_submit.py").resolve()


def _seed(mr: Path):
    from paulsha_hippo.moc import search as S
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def _run(mr: Path, payload: dict) -> dict:
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def test_copilot_prompt_hook_injects_and_attributes_tool(tmp_path):
    _seed(tmp_path)
    proj_cwd = tmp_path / "proj"; proj_cwd.mkdir(exist_ok=True)
    out = _run(tmp_path, {"sessionId": "cp1", "cwd": str(proj_cwd),
                          "prompt": "SerialWrap 執行"})
    ctx = out.get("additionalContext", "")
    assert "a.md" in ctx and "Read" in ctx
    led = (tmp_path / "runtime" / "ledger" / "offered.jsonl").read_text(encoding="utf-8")
    ev = json.loads(led.splitlines()[0])
    assert ev["tool"] == "copilot-cli" and ev["session_id"] == "cp1"


def test_copilot_prompt_hook_error_emits_empty_exit0(tmp_path):
    out = _run(tmp_path, {"sessionId": "cp2", "cwd": "/nonexistent", "prompt": "x"})
    assert out.get("additionalContext", "") == ""
```

跑 `python3 -m pytest tests/test_copilot_user_prompt_submit_hook.py -v`——預期 FAIL（HOOK 檔不存在）。

(b) 建立 `paulsha_hippo/hooks/copilot_user_prompt_submit.py`：

```python
#!/usr/bin/env python3
"""GitHub Copilot CLI prompt-time hook: inject task-relevant memory shortlist.

僅在 capability matrix（docs/cross-cli-capability-matrix.md）判定 copilot
prompt-time hook = supported 時佈署。camelCase payload；additionalContext 直出。
Any error -> empty context, exit 0.
"""
from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "copilot-cli"


def main() -> int:
    from paulsha_hippo.hooks._shortlist_common import build_shortlist_and_record
    from paulsha_hippo.hooks._wakeup_common import log_warn, memory_root, read_payload

    root = memory_root()
    payload = read_payload(root, TOOL)
    context = ""
    try:
        cwd = payload.get("cwd") or payload.get("workingDirectory")
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        prompt = str(payload.get("prompt") or "")
        context = build_shortlist_and_record(root, TOOL, session_id, cwd, prompt)
    except Exception as exc:
        log_warn(root, TOOL, f"user_prompt_submit failed: {exc}")
        context = ""

    print(json.dumps({"additionalContext": context}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

跑 `python3 -m pytest tests/test_copilot_user_prompt_submit_hook.py -v`——預期全 PASS。

(c) `paulsha_hippo/hooks/install.sh` copilot 佈線：deploy 清單（Step 3(a) 同一處）再補 `copilot_user_prompt_submit.py`；copilot 段（現行 457-459 行）加命令變數：

```bash
copilot_user_prompt_submit_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/copilot_user_prompt_submit.py"
```

heredoc 呼叫（現行 461-462 行）尾端多傳 `"$copilot_user_prompt_submit_cmd"`，heredoc 內（現行 465-469 行）加 `user_prompt_submit_cmd = sys.argv[5]`，config dict 的 `"hooks"` 內（`"preCompact"` 之後）加（事件 key 以 matrix 實測名為準，下例為 camelCase 慣例名）：

```python
        "userPromptSubmit": [
            {
                "type": "command",
                "bash": user_prompt_submit_cmd,
                "powershell": "Write-Host 'paulsha-memory: powershell path not supported in MVP'",
                "timeoutSec": 10,
            }
        ],
```

（copilot 的 uninstall 直接整檔移除 `paulsha-memory.json`，無需逐事件處理——確認方式：`grep -n "copilot" paulsha_hippo/hooks/uninstall.sh`。）

(d) 翻旗還原：`copilot_session_start.py` 的 `recall_guidance=True` 移除；`tests/test_recall_guidance.py` 的 copilot case 改斷言預設句「每次 prompt 後以短清單浮現」仍在。

(e) `tests/test_hooks.py` deploy 清單斷言補 `"copilot_user_prompt_submit.py"`，並加 copilot config 含 `userPromptSubmit` key 的斷言。

(f) 驗證與 commit：

```bash
python3 -m pytest tests/test_hooks.py tests/test_copilot_user_prompt_submit_hook.py tests/test_recall_guidance.py -v
git add paulsha_hippo/hooks/copilot_user_prompt_submit.py paulsha_hippo/hooks/install.sh \
        paulsha_hippo/hooks/copilot_session_start.py \
        tests/test_hooks.py tests/test_copilot_user_prompt_submit_hook.py tests/test_recall_guidance.py
git commit -m "feat(hooks): copilot prompt-time shortlist 接線（capability matrix 實測 supported）（#17）"
```

---

### Task 7: 文件漂移修正（openspec usage spec + README；R-18）

**Files:**
- Modify: `openspec/specs/stage2-memory-usage-telemetry/spec.md:8`（模組路徑漂移）、`openspec/specs/stage2-memory-usage-telemetry/spec.md:18-28`（usage CLI requirement）
- Modify: `README.md`（Usage 段——「日常命令：」錨行與 PR-A/B/C 跨批次共用，rebase 後不得整行覆蓋，見 Step 3 合併規則；一律以行內容定位，不以行號）

**Interfaces:**
- Consumes: Task 2-5 落地後的實際行為（文件以實作為準）
- Produces: 與實作一致的規格文字（`hippo usage` 雙 ledger、per-tool、applied、`hippo recall`）

- [ ] **Step 1: 修 openspec spec 模組路徑漂移**

`openspec/specs/stage2-memory-usage-telemetry/spec.md` 第 8 行的：

```
系統 SHALL 於 `paulshaclaw/memory/usage.py` 保留純函式 `extract_offered(brief)`
```

改為：

```
系統 SHALL 於 `paulsha_hippo/usage.py` 保留純函式 `extract_offered(brief)`
```

- [ ] **Step 2: 修 usage CLI requirement（單一 ledger 宣稱 → 雙 ledger 實況）**

同檔「### Requirement: usage 查詢 CLI」一段（現行 18-28 行）整段替換為：

```markdown
### Requirement: usage 查詢 CLI

系統 SHALL 提供 `hippo usage`（`--memory-root`、`--since`、`--json`）讀取兩個 ledger：
`runtime/ledger/offered.jsonl`（offered 事件權威，prompt-time shortlist 與 `hippo recall` 寫入）
與 `runtime/ledger/memory_usage.jsonl`（read-based `used` 事件 `source:"read"`、applied 事件
`kind:"applied"`），聚合出每 slice 的 `offered_count / read_count / last_read`（依 read 降冪）、
彙總（總 session、平均每 session read、never-read 數＝offered 過但 read=0），以及 per-tool 的
`offered / read / applied` 分列。`applied` 欄於該 tool 無任何 applied 事件時 SHALL 顯示
`n/a`（JSON 為 `null`），MUST NOT 以內容 substring 猜測補值。`read_count` SHALL 來自
read-based `used` 事件（`source:"read"`）。即使 `runtime/wakeup/*.json` 全不存在，報告
SHALL 正確（ledger 自足）。

系統 SHALL 提供 `hippo usage mark-applied`（`--memory-root`、`--session-id`、`--slice-id`、
`--tool` 皆必填）作為 applied 顯式訊號入口（agent structured acknowledgement）。寫入前
SHALL 驗證參照完整性：同 `(session_id, tool)` 於 `runtime/ledger/offered.jsonl` 存在
先行 offered 記錄、且 `slice_id` 屬於該些 offer 的 slice 集合；通過才 append 事件
`{"kind":"applied","session_id","slice_id","tool","ts"}` 至
`runtime/ledger/memory_usage.jsonl`；驗證失敗 SHALL exit 非零、MUST NOT 寫入任何事件，
並於 stderr 說明原因（防偽造 applied 事件污染漏斗遙測）。

#### Scenario: offered-but-unread 計入 never-read 且報告自足
- **WHEN** 某 slice 在 ledger 多次 offered 但從未有 read 事件，且 wakeup 檔已不存在
- **THEN** `hippo usage` SHALL 列出該 slice（offered_count>0、read_count=0）並計入 never-read 彙總

#### Scenario: read 事件計入 read_count
- **WHEN** ledger 含某 slice 的 `source:"read"` used 事件
- **THEN** 該 slice 的 `read_count` SHALL ≥1 且 `last_read` SHALL 反映最近事件時間

#### Scenario: applied 顯式訊號計入 per-tool 分列
- **WHEN** ledger 含某 tool 的 `kind:"applied"` 事件
- **THEN** per-tool 分列中該 tool 的 `applied` SHALL 為事件計數；無 applied 事件的 tool SHALL 顯示 `n/a`（JSON `null`）

#### Scenario: 偽造 applied 事件被拒
- **WHEN** `mark-applied` 指向的 `(session_id, tool)` 無先行 offered 記錄，或 `slice_id` 不在該些 offer 的 slice 集合內
- **THEN** 命令 SHALL exit 非零、MUST NOT append 任何事件，且 stderr SHALL 說明拒絕原因
```

- [ ] **Step 3: README Usage 段同步（R-18）**

`README.md` Usage 段「日常命令：」錨行（原始 main 快照為第 27 行；一律以行內容定位，不以行號）：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。
```

改為：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo recall`（跨 CLI 任務相關檢索）／`hippo search`／`hippo usage`（漏斗報表；`mark-applied` 回報 applied）／`hippo replay`／`hippo bundle`。
跨 CLI 消費能力（codex/copilot 的 prompt-time／read attribution 實測）見 `docs/cross-cli-capability-matrix.md`。
```

**合併規則（README 跨批次共用錨行；PR-A Task 12 Step 3／PR-B Task 6 Step 7／PR-C Task 7 Steps 1-2 帶同一條規則）**：若該錨行已被 sibling 批次改寫（rebase 後與上引 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段，保留 sibling 已 merge 的全部新增。本批新增片段：在命令清單 `hippo wakeup` 之後插入「／`hippo recall`（跨 CLI 任務相關檢索）」、`hippo search` 之後插入「／`hippo usage`（漏斗報表；`mark-applied` 回報 applied）」（與 PR-B 的 `hippo index verify` 同為 search 後插入片段，先後依落地順序可互換），並於錨行之後追加上引「跨 CLI 消費能力…」補充行；sibling 已 merge 的命令（`hippo index verify`／`hippo requeue …`）與其後續補充行（PR-A 蒸餾失敗顯性化／PR-C 維運）一律原樣保留，不得覆蓋或刪除。

- [ ] **Step 4: 驗證與 commit**

```bash
grep -rn "paulshaclaw/memory/usage.py\|僅讀 \`memory_usage.jsonl\`\|psc memory usage" openspec/ README.md; echo "exit=$?"
python3 -m pytest tests/ -q -x --ignore=tests/test_atomizer_llm_live.py -k "usage or recall or shortlist" 
git add openspec/specs/stage2-memory-usage-telemetry/spec.md README.md
git commit -m "docs(usage): 修文件漂移——usage 雙 ledger 實況＋hippo 命名＋README 命令清單（#17 #18）"
```

預期：grep `exit=1`（無殘留）；pytest 相關子集全 PASS。

---

### Task 8: funnel E2E——hermetic hook 鏈整合測試（進 CI）＋Claude live 實證（補充證據）

**目的**：#18 證據雙層化（adversarial review 拍板：唯一 E2E 不得綁 credential 且必須進 CI）：
(1) **hermetic 整合測試（進 CI）**——fake hook harness：以假 hook stdin payload（模擬 UserPromptSubmit / PostToolUse(Read) 事件 JSON）直接呼叫 hook 模組入口，斷言 shortlist 注入輸出 → offered 記錄 → read 事件 → mark-applied 全鏈在**無真 CLI、無 credential** 下走通；CI 綠即保護 hook wiring。
(2) **live 實證（補充證據，降級自原「唯一 E2E」）**——真實登入 claude CLI 的**平台注入**（非手動 recall）`offered → read` 記錄，可綁定 session、附 negative control，加 applied 顯式訊號至少在 Claude 平台一條實證（spec §3.6）。
**#18 關單證據＝hermetic 鏈綠（CI）＋至少一次 live 成功**；live 失敗重試預算維持原拍板（上限 2 次）。

**Files:**
- Create: `tests/test_cross_cli_funnel_integration.py`（hermetic；`tests/test_*.py` 自動進 CI——R-19 `tests.yml`）
- Create: `tests/cross_cli_live_check.sh`（credentialed 補充證據；手動、不進 CI）
- Modify: `docs/cross-cli-capability-matrix.md`（「offered → read → applied 實證」節貼入去識別證據）

**Interfaces:**
- Consumes: Task 2-5 全部落地（shortlist 尾行的 mark-applied 指引、recall CLI、mark-applied 參照完整性驗證、per-tool 報表）；`paulsha_hippo/hooks/claude_user_prompt_submit.py`、`paulsha_hippo/hooks/claude_post_tool_use.py`（既有）；live 段另需本機已登入的 `claude` CLI
- Produces: CI 常駐的 hermetic funnel 迴歸保護；去識別 live 實證輸出（matrix doc + PR body 引用）；Task 9 依「hermetic 鏈＋live applied 實證成敗」決定 `Closes #18` 與否

- [ ] **Step 1: 寫 hermetic funnel 整合測試（fake hook harness，進 CI）**

建立 `tests/test_cross_cli_funnel_integration.py`：

```python
# tests/test_cross_cli_funnel_integration.py — hermetic funnel 整合測試（PR-F Task 8）
"""offered → read → applied 全鏈 hermetic 驗證（無真 CLI、無 credential，進 CI）。

fake hook harness：以假 hook stdin payload 直接跑 hook 腳本入口——
  A. UserPromptSubmit payload → claude_user_prompt_submit.py → shortlist 注入＋offered 記錄
  B. PostToolUse(Read) payload → claude_post_tool_use.py → read 事件（offered=true、同 session）
  C. shortlist 尾行指引對應的 mark-applied → applied 事件（參照完整性驗證通過）
  D. hippo usage --json → by_tool 漏斗三欄齊備
live credentialed 腳本（tests/cross_cli_live_check.sh）僅為補充證據；本測試是 #18 的 CI 迴歸保護。
"""
import json
import subprocess
import sys
from pathlib import Path

from paulsha_hippo import cli

PROMPT_HOOK = Path("paulsha_hippo/hooks/claude_user_prompt_submit.py").resolve()
READ_HOOK = Path("paulsha_hippo/hooks/claude_post_tool_use.py").resolve()


def _seed(mr: Path) -> Path:
    from paulsha_hippo.moc import search as S
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    note = k / "a.md"
    note.write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})
    return note


def _run_hook(hook: Path, mr: Path, payload: dict) -> dict:
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def _events(mr: Path, name: str) -> list[dict]:
    f = mr / "runtime" / "ledger" / name
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_funnel_offered_read_applied_full_chain(tmp_path, capsys):
    note = _seed(tmp_path)
    proj_cwd = tmp_path / "proj"
    proj_cwd.mkdir(exist_ok=True)
    sid = "it-funnel-1"

    # A. 模擬 UserPromptSubmit：shortlist 注入＋offered 記錄（fake stdin payload）
    out = _run_hook(PROMPT_HOOK, tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": sid,
        "cwd": str(proj_cwd), "prompt": "SerialWrap 執行"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert str(note) in ctx and "Read" in ctx
    assert "usage mark-applied" in ctx  # 尾行回報指引（Task 3）
    offered = _events(tmp_path, "offered.jsonl")
    assert len(offered) == 1
    assert offered[0]["tool"] == "claude-code" and offered[0]["session_id"] == sid
    assert offered[0]["offered"] == [{"sl_id": "sl-aaaaaaaaaaaaaaaa", "path": str(note)}]

    # B. 模擬 PostToolUse(Read)：read 事件（offered=true、同 session 綁定）
    _run_hook(READ_HOOK, tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "Read",
        "tool_input": {"file_path": str(note)}})
    reads = [e for e in _events(tmp_path, "memory_usage.jsonl") if e.get("source") == "read"]
    assert len(reads) == 1
    assert reads[0]["offered"] is True and reads[0]["session_id"] == sid
    assert reads[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa"

    # C. mark-applied（同 session/tool/slice → 參照完整性驗證通過）
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", sid, "--slice-id", "sl-aaaaaaaaaaaaaaaa",
                   "--tool", "claude-code"])
    assert rc == 0
    applied = [e for e in _events(tmp_path, "memory_usage.jsonl") if e.get("kind") == "applied"]
    assert len(applied) == 1 and applied[0]["session_id"] == sid
    capsys.readouterr()  # 清掉 mark-applied 的 stdout 回顯

    # D. usage 報表：漏斗三欄齊備（offered / read / applied 全 1）
    assert cli.main(["usage", "--memory-root", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["by_tool"]["claude-code"] == {"offered": 1, "read": 1, "applied": 1}


def test_funnel_forged_applied_rejected_end_to_end(tmp_path, capsys):
    # 全鏈情境下的偽造拒絕：真的 offer 過的 session，換一個未 offer 的 slice → 拒寫
    _seed(tmp_path)
    proj_cwd = tmp_path / "proj"
    proj_cwd.mkdir(exist_ok=True)
    _run_hook(PROMPT_HOOK, tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": "it-funnel-2",
        "cwd": str(proj_cwd), "prompt": "SerialWrap 執行"})
    rc = cli.main(["usage", "mark-applied", "--memory-root", str(tmp_path),
                   "--session-id", "it-funnel-2", "--slice-id", "sl-forged0000000001",
                   "--tool", "claude-code"])
    assert rc == 1
    assert not any(e.get("kind") == "applied"
                   for e in _events(tmp_path, "memory_usage.jsonl"))
```

- [ ] **Step 2: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_cross_cli_funnel_integration.py -v
```

預期：2 個測試 PASS。本測試驗證 Task 2-5 已落地行為的**接線**（integration，非新行為——無紅燈階段）；任一 FAIL 即 hook wiring 迴歸，以 superpowers:systematic-debugging 找根因、修到綠再前進。CI 由既有 `tests.yml`（R-19）自動涵蓋本檔，hook wiring 從此有不綁 credential 的常駐保護。

- [ ] **Step 3: Commit hermetic 測試**

```bash
git add tests/test_cross_cli_funnel_integration.py
git commit -m "test(funnel): hermetic hook 鏈整合測試——offered→read→applied 無真 CLI 走通（進 CI）（#18）"
```

- [ ] **Step 4: 確認 claude CLI 旗標形式**

```bash
command -v claude && claude --version
claude --help 2>&1 | grep -i -A2 "allowedTools\|settings"
```

預期：`claude` 存在；記下 `--settings <file>` 與 `--allowedTools` 的實際形式（多值 space-separated 或 comma-separated），若與下方腳本假設不符，依 help 輸出修正腳本再跑。

- [ ] **Step 5: 寫 live E2E 腳本（補充證據）**

建立 `tests/cross_cli_live_check.sh`，內容全文：

```bash
#!/usr/bin/env bash
# cross_cli_live_check.sh — #18 live 實證（補充證據；PR-F Task 8）
# CI 迴歸保護由 hermetic 整合測試 tests/test_cross_cli_funnel_integration.py 承擔；
# 本腳本補真實 claude CLI 的平台注入實證（#18 關單＝hermetic 鏈綠＋本腳本至少一次成功）。
# 真實 adapter E2E（Claude 平台）：
#   A. 相關 prompt → UserPromptSubmit hook 注入 shortlist → offered 事件（平台注入，非手動 recall）
#   B. agent Read 該 slice → PostToolUse(Read) → read 事件（offered=true、同 session）
#   C. negative control：無關 prompt → 不新增 offered 事件
#   D. applied：agent 依 shortlist 尾行指引呼叫 hippo usage mark-applied → applied 事件
# 需本機已登入 claude CLI；手動執行、不進 CI；會消耗少量模型額度。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_BASE="$ROOT_DIR/.psc_tmp"
mkdir -p "$TMP_BASE"
TMP_DIR="$(mktemp -d "$TMP_BASE/crosscli-XXXXXX")"
trap 'rm -rf -- "$TMP_DIR"' EXIT

MEM="$TMP_DIR/memory"
PROJ="$TMP_DIR/proj"
mkdir -p "$MEM/knowledge/proj" "$PROJ"
git init -q "$PROJ"   # 讓 resolve_project 以 toplevel basename 解出 "proj"（避開外層 repo）

cat >"$MEM/knowledge/proj/serialwrap.md" <<'EOF'
---
memory_layer: knowledge
slice_id: sl-e2e0000000000001
project: proj
title: SerialWrap 埠設定
captured_at: '2026-07-10T00:00:00Z'
---
SerialWrap 的 UART 埠必須以 115200/8N1 開啟，否則靜默丟包。
EOF

PYTHONPATH="$ROOT_DIR" python3 - "$MEM" <<'PYEOF'
import sys
from pathlib import Path
from paulsha_hippo.moc import search as S
S.build_index(Path(sys.argv[1]), link_weights={})
print("[e2e] index built")
PYEOF

# 偽 hooks venv python：讓 shortlist 尾行的 mark-applied 指引在無正式安裝的暫存
# memory root 下也可被 agent 直接執行（正式部署由 install.sh 建真 venv）。
mkdir -p "$MEM/hooks/.venv/bin"
cat >"$MEM/hooks/.venv/bin/python" <<EOF
#!/usr/bin/env bash
PYTHONPATH="$ROOT_DIR" exec python3 "\$@"
EOF
chmod +x "$MEM/hooks/.venv/bin/python"

SETTINGS="$TMP_DIR/settings.json"
cat >"$SETTINGS" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command",
      "command": "PSC_MEMORY_ROOT=$MEM PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/claude_user_prompt_submit.py",
      "timeout": 10}]}],
    "PostToolUse": [{"matcher": "Read", "hooks": [{"type": "command",
      "command": "PSC_MEMORY_ROOT=$MEM PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/claude_post_tool_use.py",
      "timeout": 10}]}]
  }
}
EOF

OFFERED="$MEM/runtime/ledger/offered.jsonl"
USAGE="$MEM/runtime/ledger/memory_usage.jsonl"

echo "[e2e] A+B+D: 相關 prompt（真實 claude session）"
(cd "$PROJ" && claude -p --settings "$SETTINGS" --allowedTools "Read" "Bash" \
  "我在設定 SerialWrap 的序列埠。若系統浮現相關記憶短清單，請用 Read 開啟清單中的絕對路徑；\
若其內容影響了你的建議，請依清單末行指示執行 mark-applied 回報，然後總結建議。") || true

test -s "$OFFERED" || { echo "[e2e] FAIL: offered.jsonl 空——shortlist 未注入"; exit 1; }
grep -Fq '"tool": "claude-code"' "$OFFERED"
grep -Fq 'sl-e2e0000000000001' "$OFFERED"
echo "[e2e] offered（平台注入）OK"

grep -Fq '"source": "read"' "$USAGE" || { echo "[e2e] FAIL: 無 read 事件"; exit 1; }
grep -Fq '"offered": true' "$USAGE"
PYTHONPATH="$ROOT_DIR" python3 - "$MEM" <<'PYEOF'
import json, sys
from pathlib import Path
mem = Path(sys.argv[1])
off = [json.loads(l) for l in (mem / "runtime/ledger/offered.jsonl").read_text().splitlines() if l.strip()]
use = [json.loads(l) for l in (mem / "runtime/ledger/memory_usage.jsonl").read_text().splitlines() if l.strip()]
reads = [e for e in use if e.get("source") == "read" and e.get("offered") is True]
assert off and reads, f"missing legs: offered={len(off)} reads={len(reads)}"
bound = {e["session_id"] for e in off} & {e["session_id"] for e in reads}
assert bound, "offered/read session_id 不一致——非同一 session 綁定"
print("[e2e] offered→read 同 session 綁定 OK:", sorted(bound)[0])
PYEOF

echo "[e2e] C: negative control（無關 prompt 不觸發 offer）"
BEFORE=$(wc -l <"$OFFERED")
(cd "$PROJ" && claude -p --settings "$SETTINGS" \
  "請解釋 TCP 三次握手的流程，不需要讀任何檔案。") || true
AFTER=$(wc -l <"$OFFERED")
test "$BEFORE" -eq "$AFTER" || { echo "[e2e] FAIL: 無關 prompt 竟新增 offered（$BEFORE→$AFTER）"; exit 1; }
echo "[e2e] negative control OK（offered 行數 $BEFORE 不變）"

echo "[e2e] D: applied 實證檢查"
if grep -Fq '"kind": "applied"' "$USAGE" && grep -Fq '"slice_id": "sl-e2e0000000000001"' "$USAGE"; then
  echo "[e2e] applied 實證 OK"
else
  echo "[e2e] WARN: applied 未出現（agent 未遵循指引）——可整支重跑（上限 2 次）；"
  echo "       仍無 → PR 僅 Closes #17，#18 留 open 並留言記錄"
  echo "       （hermetic 鏈綠不足以單獨關 #18——平台實證仍缺；spec §3.6 關單條件）"
fi

echo "=== 去識別證據（貼入 docs/cross-cli-capability-matrix.md）==="
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$OFFERED" | sed 's/^/offered| /'
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$USAGE"   | sed 's/^/usage  | /'
echo "[e2e] PASS（applied 見上方判定）"
```

- [ ] **Step 6: 執行並判讀**

```bash
chmod +x tests/cross_cli_live_check.sh
bash tests/cross_cli_live_check.sh
```

預期：`offered（平台注入）OK`、`offered→read 同 session 綁定 OK`、`negative control OK`、`applied 實證 OK`（或 WARN）。applied WARN 時整支重跑，**上限 2 次**（重試預算維持原拍板）；仍 WARN → 記錄於 matrix 與 PR body，Task 9 走「僅 Closes #17」分支。**#18 關單證據＝Step 2 的 hermetic 鏈綠（CI）＋本腳本至少一次成功**——hermetic 綠但 live applied 缺，同樣不得帶 `Closes #18`。

- [ ] **Step 7: 證據落 matrix 文件**

把腳本尾段「去識別證據」輸出貼入 `docs/cross-cli-capability-matrix.md` 的「offered → read → applied 實證」節（含 negative control 的行數不變敘述與執行日期）。確認無個人絕對路徑：

```bash
grep -n "/home/" docs/cross-cli-capability-matrix.md && echo "FAIL: 有個人路徑" || echo OK
```

- [ ] **Step 8: Commit live 腳本與證據**

```bash
git add tests/cross_cli_live_check.sh docs/cross-cli-capability-matrix.md
git commit -m "test(e2e): Claude live 實證（補充證據）——offered→read＋negative control＋applied 腳本與證據（#18）"
```

---

### Task 9: 收尾——changelog 碎片＋CHANGELOG `[Unreleased]`、全量驗證、#18 retitle、PR 準備

**Files:**
- Create: `changelog.d/cross-cli-consumption.md`
- Modify: `CHANGELOG.md`（`[Unreleased]` 段——R-09 gate 以此檔為準：policy_check 的 `_unreleased_has_bullet_entry` 只檢查 `## [Unreleased]` 下有 bullet，與 changelog.d 完全無關）

**Interfaces:**
- Consumes: Task 1-8 全部產出；Task 8 的「hermetic 鏈＋live applied 實證」判定
- Produces: 可開 PR 的分支（workflow 主編排執行開 PR 與 merge；本 task 備妥 PR body 素材與 issue 操作）

- [ ] **Step 1: 新增 changelog.d 碎片**

建立 `changelog.d/cross-cli-consumption.md`（格式沿用 `changelog.d/fix-dream-service-interpreter.md` 的 `### 類別` + 條列）：

```markdown
### Added
- `hippo recall`：跨 CLI consumer API——以 prompt 檢索任務相關 shortlist 並記 `offered`（含 `--tool` 歸因），供無 prompt-time hook 的平台顯式呼叫（#17）。
- `hippo usage mark-applied`：applied 顯式訊號（agent structured acknowledgement）——ledger 事件 `{"kind":"applied","session_id","slice_id","tool","ts"}`；寫入前反查 `offered.jsonl` 做參照完整性驗證（無對應 offer 即拒寫，防偽造事件污染漏斗遙測）；shortlist 尾行注入回報指引（#18）。
- `hippo usage` 報表新增 per-tool `offered / read / applied` 分列；`applied` 無訊號時顯示 `n/a`，不做內容 substring 猜測（#18）。
- codex / copilot SessionStart 改注入顯式 recall 指引（不假裝 orientation 等同 task retrieval）；跨 CLI 能力矩陣（官方文件＋本機 probe 實測）落 `docs/cross-cli-capability-matrix.md`（#17）。

### Fixed
- 文件漂移：`hippo usage` 實際同時讀 `offered.jsonl` 與 `memory_usage.jsonl`，openspec telemetry spec 誤稱「僅讀 memory_usage.jsonl」——以實作為準修正（含 `psc memory`→`hippo`、`paulshaclaw/memory/usage.py`→`paulsha_hippo/usage.py` 命名殘留）（#18）。
```

- [ ] **Step 2: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準；比照 PR-A Task 12 Step 2）**

R-09 的 `_unreleased_has_bullet_entry` 只認 `CHANGELOG.md` 的 `## [Unreleased]` 下有 bullet——changelog.d 碎片本身**不**滿足 R-09；兩者並存：`[Unreleased]` 供 R-09 gate，碎片供 release 彙整。

把 Step 1 碎片同內容的 bullet 逐字併入 `CHANGELOG.md` 的 `## [Unreleased]`：`### Added` 四條 bullet 併入 `### Added` 標題下、`### Fixed` 一條併入 `### Fixed` 標題下（皆位於 `## [0.1.0]` 之前）。rebase 後 `[Unreleased]` 已有 sibling 批次（PR-A/B/C/D/E）的相同 `### Added`／`### Fixed` 標題時，把 bullet 併入既有標題下，**不重複標題**（R-04 格式）；標題不存在才新增標題。

- [ ] **Step 3: 全量驗證**

```bash
python3 -m pytest tests/ -q
python3 -m policy_check --repo .
```

預期：pytest exit 0、無 failed（live 測試按既有 env-gate 自動 skip）；policy_check 無任何 failure（R-09 由 Step 2 的 `[Unreleased]` bullet 滿足；碎片供 release 彙整，不滿足 R-09）。任一不過→修完再走。

- [ ] **Step 4: Commit 碎片與 CHANGELOG**

```bash
git add changelog.d/cross-cli-consumption.md CHANGELOG.md
git commit -m "chore(changelog): cross-cli-consumption 碎片＋CHANGELOG [Unreleased]（#17 #18）"
```

- [ ] **Step 5: #18 retitle（開 PR 前執行；spec §3.6 行為變更 7）**

```bash
gh issue edit 18 --title "跨 CLI offered → read → applied funnel"
gh issue comment 18 --body "根因修正記錄：matched/cited 訊號已於 consumption-loop 變更中標 deprecated（paulsha_hippo/usage.py 模組 docstring），SessionEnd recorder 已 unwired（paulsha_hippo/usage_ledger.py）——「把 matched/cited 修到非零」是錯誤方向。本 issue retitle 為漏斗交付：offered → read（per-tool 分列）＋ applied 顯式訊號（hippo usage mark-applied）。實作與實證見 PR-F（feature/17-cross-cli-consumption）。"
```

預期：issue 18 標題更新、留言成功。

- [ ] **Step 6: PR body 素材（條件式 Closes；由 workflow 主編排開 PR）**

依 Task 8 判定二選一（**不得在證據不齊時帶 `Closes #18`**；關單證據＝hermetic 鏈綠（CI）＋至少一次 live 成功）：

- **hermetic 鏈綠 且 live 實證 OK（offered→read＋applied）**：PR body 含 `Closes #17` 與 `Closes #18`，附四段證據連結（capability matrix、hermetic 整合測試（CI 綠）、live 去識別輸出、usage per-tool 報表範例）。
- **live applied 實證缺（hermetic 綠亦不足以單獨關單）**：PR body 僅 `Closes #17`＋`Refs #18`；另 `gh issue comment 18` 記錄已交付部分（hermetic funnel 鏈進 CI、offered→read live E2E、mark-applied 介面含參照完整性驗證、per-tool 報表）與剩餘缺口（applied 平台實證）。

PR checklist（`.github/pull_request_template.md`）全勾；標題建議：`feat(hooks): 跨 CLI 消費——recall API＋offered→read→applied 漏斗（#17 #18）`。

---

## 驗收對照（spec §3.6）

| spec 驗收項 | 對應 Task |
|---|---|
| codex/copilot session fixture 經 adapter 或 recall 路徑取得 shortlist 且 offered.jsonl 記錄正確 tool | Task 2（recall+tool 歸因測試）、Task 5（指引注入測試）、Task 6（條件接線測試） |
| usage 報表 per-tool 分列 | Task 4 |
| capability matrix 有實測證據 | Task 1（probe＋文件）、Task 8（實證補充） |
| #18：真實 adapter E2E offered→read（可綁 session）＋negative control | Task 8（手動 recall 不算——live 用 Claude UserPromptSubmit 平台注入；hermetic 全鏈整合測試另進 CI 常駐保護 hook wiring） |
| #18：applied 介面交付（含 anti-forgery 參照完整性）＋Claude 平台實證 | Task 3（介面＋驗證）、Task 8（hermetic 鏈＋live 實證）、Task 9（條件式關單） |
| 漂移修正（usage 雙 ledger 文件） | Task 7 |
| #18 retitle 時序（開 PR 前） | Task 9 Step 5 |
