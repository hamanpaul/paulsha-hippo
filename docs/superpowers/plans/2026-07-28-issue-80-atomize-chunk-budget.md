---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# 大 session 的 chunk 序列預算與部分成果保留 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task — write the failing test first, watch it fail for the right reason, then implement. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓有真實 ingress 的 dream timer cycle 能穩定產出至少一個 accepted atom：大 session 的 fallback 鏈拿得到與其 chunk 數相稱的時間、明顯做不完的 profile 不再空耗預算、已驗證的 chunk 成果不因後段失敗而整批丟棄。

**Architecture:** 三個 Task 依風險遞增排序，彼此可獨立交付。Task 1 只動 `agent_profiles.py` 的常數與 `run_session` 的預算算式；Task 2 新增一個宣告式 profile 欄位並接進既有的 ineligible 路徑；Task 3 改 router 的 session 契約（已驗證前綴可保留、跨 profile 續跑）並讓 provenance 誠實反映混合 profile。**做不完 Task 3 不影響 Task 1、2 的價值**——請依序完成並各自保持全綠，不要為了 Task 3 而讓前兩者處於半完成狀態。

**Tech Stack:** Python 3.12、標準庫、pytest。無新增外部相依。

## Global Constraints

- **語言**：PR 標題、內文、commit message、changelog 碎片一律 zh-tw（repo 屬 `github.com/hamanpaul/*`）。
- **TDD 強制**：每個 Task 先寫測試看到 RED 再實作。測試通過即定案，不追加超出 Task 範圍的重構。
- **不得放寬 `FIXED_TIMEOUT_SECONDS`**（單次呼叫 300s hang 防護）。任何改動若使單次呼叫可超過 300s 即為違規。
- **不得新增 fallback category**。`FALLBACK_ON` 是不可變 allowlist，新的不適用情境沿用既有的 `ineligible`。
- **不得弱化既有失敗語意**：耗盡仍須 park、仍須寫 parked evidence 與 `attempts_detail`，`failure_kind` 分類不變。
- **出貨模板漂移守門**：`atomizer.yaml` 的 profile argv 必須與 `agent_profiles.default_profiles()` 逐 token 相同（`tests/test_external_agent_profiles.py::test_packaged_config_template_argv_matches_canonical_defaults`）。改 profile 定義時兩處必須同步。
- **交付治理**：同一 PR 必須新增 `changelog.d/<slug>.md` 碎片；`python3 -m pytest -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全數通過；PR body 用 repo 的 `.github/pull_request_template.md` 且 checklist 全勾；PR body 以 `Closes #80` 關閉 issue。
- **測試環境**：跑全套測試請使用非巢狀的 sibling worktree。在 repo 目錄底下的巢狀 worktree（`.claude/worktrees/*`）執行時，`tests/test_project_resolver.py::ResolveAutoDetectTests` 會有 2 個固定失敗，那是環境假訊號而非缺陷。
- **清理**：測試會生出 `.psc_tmp/`（`tests/stage2_integration_check.sh` 無 trap 清理且未進 `.gitignore`）。commit 前務必 `rm -rf .psc_tmp`，且**不要用 `git add -A`**。

## Task 1: session 預算隨 chunk 數縮放

**檔案**：`paulsha_hippo/agent_profiles.py`、`tests/test_external_agent_profiles.py`

- [ ] 先寫測試：以 stub executor 建構 `ExternalAgentRouter`，斷言 `run_session` 對 1、2、7、100 個 prompt 分別採用的有效 deadline 為 600、600、1680、1800 秒。建議做法是把有效預算抽成可單獨呼叫的純函式（例如 `session_deadline_seconds(chunk_count)`）以便直接斷言，router 內部改呼叫它。
- [ ] 先寫測試：斷言單次呼叫拿到的 timeout 仍是 `min(profile.timeout, remaining_seconds)` 且永不超過 `FIXED_TIMEOUT_SECONDS`。
- [ ] 實作：新增 `FIXED_PER_CHUNK_DEADLINE_SECONDS = 240` 與 `FIXED_SESSION_DEADLINE_CAP_SECONDS = 1800`；`FIXED_SESSION_DEADLINE_SECONDS = 600` 維持不變並成為下限。
- [ ] 實作：`run_session` 以 `min(CAP, max(FLOOR, PER_CHUNK * len(frozen_prompts)))` 取代直接使用 `self.deadline_seconds`。空序列的早退分支維持不變。
- [ ] 在常數處補註解說明三個數字的來源（實測 190.5s／244s 單 chunk、每小時 timer 視窗、hang 防護不動）。
- [ ] 全套測試綠。

## Task 2: profile 以 `max_session_chunks` 宣告適用範圍

**檔案**：`paulsha_hippo/agent_profiles.py`、`paulsha_hippo/atomizer/atomizer.yaml`、`paulsha_hippo/atomizer/config.py`、`tests/test_external_agent_profiles.py`

- [ ] 先寫測試：`AgentProfile.from_mapping` 接受正整數 `max_session_chunks`；`0`、負數、非整數一律 `ProfileConfigError`；缺省時為 `None`。
- [ ] 先寫測試：`profile.eligible(task_class="atomization", chunk_count=7)` 在 `max_session_chunks=6` 時回 `(False, "session_size")`，`chunk_count=6` 或 `chunk_count=None` 時回 `(True, "eligible")`。
- [ ] 先寫測試：router 對 7-chunk session 跳過 `max_session_chunks=6` 的 profile 時，該 attempt 進 `attempts` provenance（`failure_category="ineligible"`）且 **agent call 計數不增加**，並繼續 fallback 到下一個 profile。
- [ ] 先寫測試：canonical default 的 `claude` 與 `codex` 皆為 `max_session_chunks=6`，`cg` 為 `None`；且 `atomizer.yaml` 與 canonical default 一致（沿用既有的模板漂移守門測試風格）。
- [ ] 實作：`AgentProfile` 新增欄位與驗證、`eligible()` 新增 keyword-only `chunk_count`、`run_session` 以 `len(frozen_prompts)` 傳入。
- [ ] 實作：`cache_namespace()` 的 profile 描述加入 `max_session_chunks`（路由語意變更不得跨快取重放）。**不要**加進 `command_fingerprint`／`cache_identity`——它不改變送出的命令，混入會使既有快取全數失效。
- [ ] 實作：`atomizer.yaml` 與 `config.py` 同步。
- [ ] 全套測試綠。

## Task 3: 已驗證的 chunk 成果跨 profile 保留與續跑

**檔案**：`paulsha_hippo/agent_profiles.py`、`paulsha_hippo/atomizer/agent_exec.py`、`paulsha_hippo/atomizer/llm_promoter.py`、對應測試

- [ ] 先寫測試：三個 chunk 的 session，profile A 完成 chunk 0、1 後在 chunk 2 失敗；斷言 profile B 收到的 prompt 序列**只有 chunk 2**，且最終回傳的 outputs 為 `[A0, A1, B2]`。
- [ ] 先寫測試：上述情境的 provenance 記得出每個 chunk 的 profile，且 session 層 `fallback_reason` 為既有的 `degraded-success`。
- [ ] 先寫測試：`CachingAgentClient` 在 chunk 驗證通過當下即落地該 chunk 的快取（不必等整個 session 成功），且不同 profile 產出的 chunk 不互相污染。
- [ ] 先寫測試（回歸）：所有 profile 都無法完成剩餘 chunk 時仍拋 `AgentRunError`、pipeline 仍 park、`attempts_detail` 的 `failure_kind` 分類不變。
- [ ] 實作：`run_session` 以「已驗證前綴 + 續跑起點」取代「全有全無」；保留 frozen prompt 序列與 per-chunk 驗證不變。
- [ ] 實作：per-chunk provenance 結構與其在 `agent_exec` / `llm_promoter` 的傳遞；slice `distiller` 欄位誠實反映混合 profile。
- [ ] 實作：`CachingAgentClient` 改為 per-chunk 落地。
- [ ] 全套測試綠。

## Task 4: 交付治理

- [ ] `changelog.d/80-atomize-chunk-budget.md`：zh-tw，說明三個 Task 的實際落地範圍與量測依據（若 Task 3 未完成，誠實只寫已完成的部分）。
- [ ] `rm -rf .psc_tmp`，逐檔 `git add`（不得 `git add -A`）。
- [ ] 非巢狀 worktree 跑 `python3 -m pytest -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict`，三者皆綠。
- [ ] PR body 用 `.github/pull_request_template.md`，checklist 全勾，內含 `Closes #80`，並附上實際測試輸出數字（不得寫「應該會過」）。

## Blockers

- [ ] 無
