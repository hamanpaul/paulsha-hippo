---
name: add-new-harness
description: Use when adding a new agent CLI to paulshaclaw footer and cockpit usage, or to paulsha-hippo note injection and session capture. Triggers include a new harness, creditUsagePercent, systemMessage, SessionEnd versus Stop, and capability-matrix honesty.
---

# 把新 harness 接進用量與 hippo

先讀該平台自己的文件與一筆去識別的本機 payload。Claude 的 `additionalContext`、`hookSpecificOutput`、5h+週池雙視窗，都不是預設契約。

兩份本 skill 必須保持相同：`paulshaclaw/custom-skills/add-new-harness/SKILL.md` 與 `paulsha-hippo/custom-skills/add-new-harness/SKILL.md`。不要另同步到 `hamanpaul/custom-skills`，除非使用者另外要求。

## 用量（paulshaclaw）

1. 找到已是百分比的本機欄位與重置時間。`creditUsagePercent: 2.0` 就是 2%，不要再乘 100。新鮮度看該行時間，不看檔案 mtime，也不要為了刷新去跑 headless。
2. 只輸出平台真正有的視窗。沒有 5h 就走週池段 `name wk:N%(reset)`，不要印 `5h:--`。逾時仍顯示數字並在名稱後加 `~`；沒有可用行才是 `wk:--`。
3. footer 與 cockpit 都吃同一份 snapshot。cockpit 帶 ANSI，測試先剝色再比對文字。
4. 新 provider 若預設 enabled，所有會呼叫 `collect_all(CostConfig())` 的測試都要明確關掉，否則會讀到開發機的活日誌。
5. 一起改：config、collector、degraded snapshot、enabled filter、`_FOOTER_PROVIDERS`、TUI 選項、sample yaml、README。`--footer` 沒點到這個名字時要寫入 `enabled: false`。
6. 既有 spec 若寫「全部 N 個 provider」，把句子改成仍然為真。不要為了這件事新開一個未完成的 OpenSpec change。

## 筆記與 session（paulsha-hippo）

1. tool id 用 path-safe token。`validate_tool` 已接受。加進 usage funnel 的工具 tuple。不要加進歷史 legacy lock 名單。
2. 對照該平台 hook 文件，只送它會讀的 stdout。Grok 的 SessionStart 只讀頂層 `{"systemMessage": "..."}`，空字串也要印。沒有 prompt-time hook 時，`compute_brief_and_record(..., recall_guidance=True)`。
3. `UserPromptSubmit` 的 `additionalContext` 若會被丟棄，不要裝 prompt-time injector。
4. 截取用不會把 agent 留在工作中的事件。Grok 的 Stop exit 2 會讓它繼續，所以用 SessionEnd 與 PreCompact，並在 `subagentType` 非空時跳過。
5. SessionEnd 腳本在 module level 只用標準庫。`HIPPO_SELF_SESSION` 在 package import 之前返回。寫 queue、補 git snapshot、背景叫 importer，永遠 exit 0。
6. PostToolUse 的 stdout 若會取代工具輸出，記帳腳本不准印任何東西。matcher 用該平台的讀檔工具名。
7. `install.sh` 部署腳本，並整檔覆寫該平台的 hook 檔（Grok 是 `~/.grok/hooks/paulsha-memory.json`）。不要寫 `managedBy`、`if`、`args`。`uninstall.sh` 只刪這一檔。
8. adapter 讀該平台 transcript，略過合成列。`pipeline._extract` 要分派到它。測試用 fixture，不讀 `~/.grok`。
9. capability matrix：`supported` 要文件加上本機 probe FIRED。沒做 MEMO-TOKEN 送達 probe 就寫 inconclusive，總評不要因為有 shell 就升成 recall-capable。read attribution 可以依文件判定，但要寫明 log-only、沒有 live probe。

## 出荷

- 分支 `feature/<slug>`，不要 commit 到 `main`。先查 issue；沒有就不要開，PR 正文不要寫裸 `#N`。
- code 變更加 `changelog.d/<slug>.md`。文件與 PR 用繁體中文。這兩個 repo 的 CLAUDE.md 沒有要求 `Co-authored-by`。
- 起草 PR 標題、正文、labels、base 之後跑 `~/.agents/skills/preflight-ci/scripts/preflight.sh`。看到 `PREFLIGHT PASS` 才 push。

## 常見錯法

| 錯法 | 結果 |
|---|---|
| 套 Claude 的 `hookSpecificOutput.additionalContext` | Grok SessionStart 讀不到 |
| 用量再乘 100，或補一個 `5h:--` | 百分比失真，footer 出現不存在的視窗 |
| 用 Stop 截取 | exit 2 讓 agent 繼續工作 |
| PostToolUse `print` JSON | 取代模型看到的工具輸出 |
| 測試不關新 provider | `collect_all` 讀到本機活日誌 |
| 沒 probe 就寫 supported | 消費能力被說成已送達 |
