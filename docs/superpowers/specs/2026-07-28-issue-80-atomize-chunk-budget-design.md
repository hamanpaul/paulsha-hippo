---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# 大 session 的 chunk 序列預算與部分成果保留（issue #80）設計

- 日期：2026-07-28
- Issue：[#80](https://github.com/hamanpaul/paulsha-hippo/issues/80)
- 狀態：已核可，待實作

## 背景

三個相鄰但不同的缺陷已分別定位：#75（chain 預算被單次呼叫上限封頂，已修）、#74（`local-vllm` map-reduce 只切輸出不切輸入，未修、不在本次範圍）、以及本案。本案處理的是「時間夠不夠」與「已完成的部分算不算數」，證據見 spec。

三個方向依風險遞增排序落地：預算縮放（最小、純常數與算式）→ profile 適用性宣告（中等、動到 eligibility）→ 部分成果保留（最深、動到 router 的 session 契約）。順序本身即是設計決定：前兩者可獨立產生價值，第三者做不完也不影響前兩者已交付的部分。

## Decisions

### D1：session 預算由 chunk 數決定，per-call 上限不動

`ExternalAgentRouter.run_session(prompts)` 在呼叫當下就知道 `len(prompts)`，因此有效預算改為在 `run_session` 內依 chunk 數計算，而非固定用建構子傳入的常數。

```
effective_deadline = min(
    FIXED_SESSION_DEADLINE_CAP_SECONDS,
    max(FIXED_SESSION_DEADLINE_SECONDS, FIXED_PER_CHUNK_DEADLINE_SECONDS * len(prompts)),
)
```

- `FIXED_SESSION_DEADLINE_SECONDS = 600` 維持不變，成為**下限**（單／雙 chunk 的小 session 行為完全不變，向後相容）。
- 新增 `FIXED_PER_CHUNK_DEADLINE_SECONDS = 240`：取自實測 190.5s／244s 的上緣，讓 tier-1 有機會跑完而不是必然餓死。
- 新增 `FIXED_SESSION_DEADLINE_CAP_SECONDS = 1800`：上限。dream timer 每小時觸發，單一 session 不得吃掉半小時以上的視窗，否則同輪其他 session 全被排擠。7 chunks → `min(1800, max(600, 1680)) = 1680s`。
- `FIXED_TIMEOUT_SECONDS = 300` 完全不動，`_run_one` 仍收 `min(profile.timeout, remaining_seconds)`。hang 防護不受影響。

`external_agents.deadline_seconds` 的 config 驗證維持既有語意（它界定的是下限那個值），不新增可調參數——這些是契約常數，不是 operator 旋鈕。

### D2：profile 以宣告式 `max_session_chunks` 表達適用範圍

`AgentProfile` 新增欄位 `max_session_chunks: int | None = None`（`None` = 不限）。`eligible()` 增加一個 keyword-only 參數 `chunk_count: int | None = None`；當 `max_session_chunks` 有值且 `chunk_count` 超過它時回 `(False, "session_size")`。

- router 在 `run_session` 內以 `len(frozen_prompts)` 呼叫 `eligible(...)`，不適用的 profile 走既有的 ineligible 路徑：**記入 attempts provenance、消耗零個 agent call**，與現行 `disabled`／`task_class` 的處理一致。
- 失敗類別沿用既有的 `ineligible`（已在 `FALLBACK_ON` 內），不新增 fallback category，避免動到不可變的 transition allowlist。
- 出貨模板與 canonical default 只為 tier-1 設值（依 240s／chunk 與 1800s 上限推算，`claude` 與 `codex` 皆設 `max_session_chunks: 6`）；`cg`（26s/chunk）不設，維持不限。
- `max_session_chunks` 進入 `command_fingerprint`／`cache_identity` 的判定？**不進**。它不改變送給 agent 的命令，只影響路由；混入指紋會讓既有快取全數失效。但它必須進 `cache_namespace()` 的 profile 描述，理由與 `fallback_on` 相同：路由改變時快取不得跨語意重放。

### D3：已驗證的 chunk 成果可跨 profile 續用

`run_session` 的「全有全無」語意改為「已驗證的前綴可保留」：

- 維持既有的 per-chunk 驗證（`_validate_response`）與 frozen prompt 序列不變。
- 某個 profile 在第 k 個 chunk 失敗時，`0..k-1` 的已驗證輸出連同「產出它們的 profile」一併保留；下一個 profile **從第 k 個 chunk 開始**，不再從 chunk 0 重跑。
- 全部 chunk 完成後回傳的仍是完整的 outputs 序列，呼叫端（`llm_promoter`）不需要改變其消費方式。
- provenance 誠實化：新增 per-chunk 的 `chunk_provenance`（`chunk_index` → `profile_id` / `tier` / `elapsed_seconds`），並在多於一個 profile 貢獻時把 session 層的 `fallback_reason` 記為既有的 `degraded-success`。**不得**把混合 profile 的 session 記成單一 profile 產出——那會讓 slice frontmatter 的 `distiller.profile_id` 說謊。
- 快取：`CachingAgentClient` 已是 per-chunk cache key，改為「每個 chunk 驗證通過即可落地」，而非整個 session 成功才落地。cache key 內既有的 profile 維度維持不變，因此不同 profile 產出的 chunk 不會互相污染。
- 真正耗盡（所有 profile 都無法完成剩餘 chunk）時的行為不變：拋 `AgentRunError`、pipeline park session、寫 parked evidence 與 `attempts_detail`。

## 元件

| 檔案 | 變更 |
|---|---|
| `paulsha_hippo/agent_profiles.py` | 新增兩個預算常數與 `max_session_chunks` 欄位；`eligible()` 增 `chunk_count`；`run_session` 改用有效預算、ineligible-by-size、續跑與 per-chunk provenance |
| `paulsha_hippo/atomizer/agent_exec.py` | `CachingAgentClient` 改為 per-chunk 落地，並攜帶 per-chunk provenance |
| `paulsha_hippo/atomizer/llm_promoter.py` | 消費 per-chunk provenance，寫入 slice `distiller` 欄位 |
| `paulsha_hippo/atomizer/atomizer.yaml` | tier-1 profile 補 `max_session_chunks: 6`（受 argv 漂移守門保護，需與 canonical default 一致） |
| `paulsha_hippo/atomizer/config.py` | profile 設定解析接受並驗證 `max_session_chunks`（正整數或缺省） |

## 錯誤處理

- `max_session_chunks` 非正整數 → `ProfileConfigError`（fail-closed，與既有 profile 欄位驗證一致）。
- 有效預算計算不得因 `len(prompts) == 0` 而回 0：空序列在 `run_session` 既有的早退分支就已回傳，不進入預算計算。
- per-chunk provenance 缺漏時不得靜默：若某個已回傳的 chunk 沒有對應的 provenance 記錄，視為程式錯誤而非可容忍狀態。

## 測試

每個 Task 均需先寫失敗測試再實作（見 plan）。至少涵蓋：

- 單 chunk 與雙 chunk session 的有效預算等於 600s（向後相容）；7 chunks 等於 1680s；100 chunks 被 cap 在 1800s。
- `FIXED_TIMEOUT_SECONDS` 不受影響：單次呼叫拿到的仍是 `min(300, remaining)`。
- `max_session_chunks` 超過時 profile 判 `(False, "session_size")`，該 attempt 進 provenance 且 agent call 計數不增加。
- 第 k 個 chunk 失敗後，次一 profile 由第 k 個 chunk 開始（以 executor 收到的 prompt 序列斷言），且前段輸出不重跑。
- 混合 profile 完成的 session，provenance 記得出每個 chunk 的 profile，且 session 層標記 `degraded-success`。
- 全數耗盡時仍 park，`attempts_detail` 的 `failure_kind` 分類不變。

## 不在範圍

- `FIXED_TIMEOUT_SECONDS` 的值、chunk 打包演算法、`local-vllm`（#74）、以及 AR-11 soak 本身的累積（那是本案落地後的自然結果，不是本案的驗收項）。

## 合併後的 runtime 待辦

- 同步使用者 live config `~/.config/paulsha-hippo/config.yaml`（repo 外）補上 tier-1 的 `max_session_chunks`，否則部署端沿用舊設定。
- 觀察後續三輪有 ingress 的 timer cycle 是否產出 accepted atom，作為 AR-11 的前置條件。
