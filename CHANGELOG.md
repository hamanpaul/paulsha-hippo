# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [0.1.0] - 2026-07-07

### Added
- #125 Phase 1 code 遷入：paulshaclaw `memory/**`（31k LOC、872 tests 綠）平移為 `paulsha_hippo/**`；`lifecycle` → `lib/lifecycle`、`idle` → `lib/idle`；hippo CLI 樹去 `memory` 前綴（`hippo atomize|dream|janitor|replay|bundle|search|wakeup|syncback|knowledge`）；`paths.py` 單一權威 resolver（`HIPPO_*` > `PSC_*` deprecated 警告 > config.yaml > `~/.agents/memory`）；stage2 12 份 capability specs、integration check、gemma4 wrapper（scripts/ + examples/）隨遷
- 隨遷主 repo `tests/` 下漏網的 memory 測試：policy 三件（boundary/redaction/lint/cli，54 tests）與 #218 hooks 截取自足回歸 2 件
- quickstart 面：`hippo init`（backend preset 寫入 atomizer override）、`hippo doctor`（雙 root FAIL 健檢）、`hippo install hooks|service`（systemd 偵測＋`dream supervise` fallback）、`hippo dream supervise`（前景常駐）；蒸餾三檔位——`claude-headless`（零 key）、`openai-compatible`（stdlib http-runner）、`custom-argv`（既有 agent_exec）
- repo 骨架：conventions 引擎 1.0.12（pin 5829015）+ `tier: shareable`（R-21 deident gate day-1）、package 0.1.0 與 `hippo --version` 入口、版號一致性測試、`paulsha_hippo.lib` import 隔離護欄、骨架期 README
- `paulsha_hippo.lib.session_readers`：`read_codex_rollout`/`read_copilot_history` 升格 lib API（hippo importer + paulshaclaw bro hook 兩使用者；adapters.base 保留 re-export）

### Fixed
- #7 遞迴自捕捉：agent_exec 對蒸餾子程序注入 `HIPPO_SELF_SESSION=1`，5 個 capture hook（session_end×3／precompact×2）讀到即早退（layer 1）；importer 對 prompt 內容即 atomize skill 調用文本者 `self-skip`（layer 2）
- #8 空 session 汙染：importer 對無 prompt/無 touched files/summary 空或佔位/turn≤1 的 session `empty-skip`，不寫 inbox、不入蒸餾佇列
- wheel/pipx 情境 `hippo install hooks`：repo_root 無 pyproject 時 importer venv 改複製已解包套件（原 pip install -e 必失敗）；sample yaml 隨包（config-samples/）
- wheel 安裝缺非 .py 資產（hooks install.sh／dream systemd 範本／atomizer.yaml／skills）——補 package-data 宣告；fresh-install E2E 驗證抓到
