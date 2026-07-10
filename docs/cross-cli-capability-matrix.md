# 跨 CLI 消費能力矩陣（capability matrix）

> 實查日期：2026-07-11（copilot prompt-time 於同日以官方事件 key 復測，推翻先前錯誤 key 的判定，見證據區）。
> probe 腳本：`tests/cross_cli_probe_check.sh`；證據為當日本機輸出（路徑已以 `<tmp>` / `<repo>` / `~` 去識別）。
> 判定規則：`supported` = 官方文件列出該事件 **且** 本機 probe FIRED；僅其一 = `inconclusive`（保守處理，等同不支援，不接線）。
> `read attribution` 依 Task 1 Step 3 僅以官方文件判定（不強求 fire 實測）；copilot 另有 live 實測（見漏斗實證區）。

| 能力 | claude-code | codex | copilot-cli |
|---|---|---|---|
| session-start 注入 | supported（SessionStart，既有佈署） | inconclusive（文件列 `SessionStart`；headless probe 對照組未 fire） | supported（文件列 `sessionStart`/`SessionStart`；probe FIRED） |
| prompt-time shortlist（自動） | supported（UserPromptSubmit，既有佈署） | inconclusive（文件列 `UserPromptSubmit`；headless probe 未能 fire） | supported（文件列 `userPromptSubmitted`；官方 key probe FIRED＋additionalContext 注入實測；已接線 `copilot_user_prompt_submit.py`） |
| read attribution | supported（PostToolUse(Read)，既有佈署） | not-supported（`PostToolUse` 僅涵蓋 Bash / `apply_patch` / MCP；非 `Read` 等價） | supported（`postToolUse` payload 實測 `toolName:"view"`＝Read file contents；已接線 `copilot_post_tool_use.py`，view 過濾在腳本內做） |
| 顯式 recall（`hippo recall`） | supported | supported（session-start 指引注入，PR-F） | supported（CLI 可用；自動 shortlist 接線後 session-start 還原預設提示） |
| applied 顯式訊號（`hippo usage mark-applied`） | supported（PR-F，含實證） | 介面可用（無平台注入實證） | supported（PR-F，含實證——live 漏斗 applied OK） |
| 總評 | full | recall-capable | full |

> 總評語意：`full`＝自動 shortlist＋read attribution 全鏈；`recall-capable`＝無 prompt-time hook，
> 但 agent 可依 session-start 指引顯式呼叫 `hippo recall`；`produce-only`＝連 recall 都不可行
>（該平台 agent 無 shell 工具）。**不假裝 SessionStart orientation 等同 task retrieval。**

## 證據

### codex
- CLI 版本：`codex-cli 0.144.1`
- 官方文件依據：
  - `openai/codex` `README.md` 的 Docs 區塊指向 Codex 官方文件；repo `docs/config.md` 的 `## Lifecycle hooks` 明列 hooks 為正式設定面。
  - `https://learn.chatgpt.com/docs/hooks`（由 `https://developers.openai.com/codex/hooks` / `https://platform.openai.com/docs/codex/hooks` 轉址）列出事件：`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PreCompact`、`PostCompact`、`SessionStart`、`UserPromptSubmit`、`SubagentStart`、`SubagentStop`、`Stop`。
  - `openai/codex` repo `codex-rs/hooks/src/lib.rs` 的 `HOOK_EVENT_NAMES` 與官方 hooks 頁一致；同頁另述 `PostToolUse`「runs after supported tools produce output, including Bash, apply_patch, and MCP tool calls」且「doesn’t intercept ... other non-shell, non-MCP tool calls」，因此不等價於 `PostToolUse(Read)`。
  - 本機 `codex --help`、`codex exec --help` 已擷取；help 另列 `--dangerously-bypass-hook-trust`，與 hooks trust gate 行為一致。
- probe 輸出（去識別）：

```text
=== codex version: codex-cli 0.144.1 ===
codex
ok
tokens used
5,546
ok
[probe] codex SessionStart（對照）: INCONCLUSIVE（對照組也沒 fire → harness/auth/trust 問題，未能實測）
[probe] codex prompt-time hook: INCONCLUSIVE（對照組也沒 fire → harness/auth/trust 問題，未能實測）
```

- follow-up：依 Step 2 註記補做 headless trust/approval 排除 rerun（`codex exec --dangerously-bypass-hook-trust`；再加 `--ask-for-approval never`）仍回 `Permission denied and could not request permission from user`，故最終維持 `inconclusive`。

### copilot-cli
- CLI 版本：`GitHub Copilot CLI 1.0.70`
- 官方文件依據：
  - `https://docs.github.com/en/copilot/reference/hooks-reference` 說明 `~/.copilot/hooks/*.json`（或 `COPILOT_HOME/hooks/`）hook 設定，並明列事件：`sessionStart`、`sessionEnd`、`userPromptSubmitted`、`preToolUse`、`postToolUse`、`postToolUseFailure`、`permissionRequest`、`preCompact`、`subagentStart`、`subagentStop`、`agentStop`、`errorOccurred`、`notification`。
  - 同頁 payload 區段以 `userPromptSubmitted` / `UserPromptSubmit` 為 prompt 事件名。
  - 同頁 `postToolUse` 區段明列 `Matcher: Optional regex tested against toolName`；`Tool names for hook matching` 表列 `view` = `Read file contents`，故具備 read attribution 所需的事件與工具名稱可辨識性。
  - 本機 `copilot --help` 已擷取；事件 key 以官方 hooks reference 為準。
- **判定勘誤（2026-07-11 復測）**：本 task 首輪 probe 依 plan 草稿使用 `userPromptSubmit`——該 key 不在官方事件表，NOT-FIRED 只證明「不存在的 key 不會 fire」，不構成 prompt-time 不支援的證據（對照組 fire 僅證明 harness 有載入設定）。已將 probe 改為官方 key `userPromptSubmitted` 復測 → FIRED；首輪 NOT-FIRED 判定作廢。
- probe 輸出（去識別；官方 key 復測）：

```text
=== copilot version: GitHub Copilot CLI 1.0.70. ===

Changes    +0 -0
Requests   0 Premium (18s)
Tokens     ↑ 57.3k (27.6k cached) • ↓ 289 (261 reasoning)
Resume     copilot --resume=7714748b-bfba-4966-8904-068ba7de9c31
[probe] copilot sessionStart（對照）: FIRED（支援）
[probe] copilot prompt-time hook: FIRED（支援）
[probe] done — 將上列輸出（已去識別）貼入 docs/cross-cli-capability-matrix.md 證據區
```

- payload schema 實測（`userPromptSubmitted` / `postToolUse` hook stdin 原文捕捉，2026-07-11）：

```text
userPromptSubmitted| {"sessionId":"d44d38f8-...","timestamp":1783716620220,"cwd":"<tmp>/work","prompt":"read the file note.md ..."}
postToolUse        | {"sessionId":"d44d38f8-...","timestamp":...,"cwd":"<tmp>/work","toolName":"view","toolArgs":"{\"path\":\"<tmp>/work/note.md\",\"view_range\":[1,5]}","toolResult":{"resultType":"success","textResultForLlm":"..."}}
```

  （注意 `toolArgs` 是 JSON 字串；adapter 需二次解析。）
- additionalContext 注入實測（2026-07-11）：`userPromptSubmitted` hook stdout 輸出
  `{"additionalContext": "MEMO-TOKEN: zebra-quantum-42"}`，headless prompt 詢問 context 中的
  MEMO-TOKEN，模型回覆 `zebra-quantum-42` → 證明 prompt-time hook 輸出確實進入模型 context
  （非僅 fire）。同一鏈的行為級證據見下方 live 漏斗（shortlist 注入後 agent 直接 view 清單路徑）。

### claude-code（既有佈署，列作基線）
- UserPromptSubmit / PostToolUse(Read) 由 `paulsha_hippo/hooks/install.sh` 佈署（Step 4 / matcher="Read"）。
- 真實 adapter E2E 證據見下方「offered → read → applied 實證」（Task 8 產出時附）。

## offered → read → applied 實證（Task 8 填；copilot 接線後同鏈復驗）

- 執行日期：`2026-07-11`（claude 初證＋copilot 接線後兩平台同腳本復驗）
- hermetic 證據：`tests/test_cross_cli_funnel_integration.py`（claude＋copilot 兩條 adapter 鏈）已納入 `tests.yml`，常駐保護 hook wiring。
- live 補充證據：`bash tests/cross_cli_live_check.sh`（兩平台各自）
  - `offered（平台注入）OK`
  - `offered→read 同 session 綁定 OK`
  - `negative control OK（offered 行數 1 → 1，不變）`
  - `applied 實證 OK`

claude：

```text
offered| {"ts": "2026-07-10T20:12:49.017332+00:00", "session_id": "8d631067-2ac9-428e-a42e-fadde17e49de", "tool": "claude-code", "project": "proj", "offered": [{"sl_id": "sl-e2e0000000000001", "path": "<tmp>/memory/knowledge/proj/serialwrap.md"}]}
usage  | {"ts": "2026-07-10T20:12:54.883605+00:00", "session_id": "8d631067-2ac9-428e-a42e-fadde17e49de", "tool": "claude-code", "project": "proj", "sl_id": "sl-e2e0000000000001", "path": "<tmp>/memory/knowledge/proj/serialwrap.md", "source": "read", "offered": true}
usage  | {"kind": "applied", "session_id": "8d631067-2ac9-428e-a42e-fadde17e49de", "slice_id": "sl-e2e0000000000001", "tool": "claude-code", "ts": "2026-07-10T20:13:03.158621+00:00"}
```

copilot（`userPromptSubmitted` 注入 → `postToolUse(view)` 歸因 → agent 依尾行指引 mark-applied；
注入成功時 session 42s／未注入對照跑 3-5 分鐘盲找，行為差異即注入的第二重證據）：

```text
offered| {"ts": "2026-07-10T21:40:12.113806+00:00", "session_id": "9736b20a-c9ba-4ce5-89a1-edcbb1f70d8c", "tool": "copilot-cli", "project": "proj-cp", "offered": [{"sl_id": "sl-e2e0000000000002", "path": "<tmp>/memory-cp/knowledge/proj-cp/serialwrap.md"}]}
usage  | {"ts": "2026-07-10T21:40:36.776516+00:00", "session_id": "9736b20a-c9ba-4ce5-89a1-edcbb1f70d8c", "tool": "copilot-cli", "project": "proj-cp", "sl_id": "sl-e2e0000000000002", "path": "<tmp>/memory-cp/knowledge/proj-cp/serialwrap.md", "source": "read", "offered": true}
usage  | {"kind": "applied", "session_id": "9736b20a-c9ba-4ce5-89a1-edcbb1f70d8c", "slice_id": "sl-e2e0000000000002", "tool": "copilot-cli", "ts": "2026-07-10T21:40:40.336291+00:00"}
```
