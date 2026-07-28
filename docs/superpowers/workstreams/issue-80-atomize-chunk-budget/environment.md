---
status: accepted
work_item: issue-80-atomize-chunk-budget
---

# issue-80-atomize-chunk-budget / 派工環境前置

2026-07-28 實測，以 cortex work-item 路徑派工本批次時踩到的環境前置。與實作內容無關，但不先處理就派不出去。

## Decisions

- **builder 身分只能由 `~/.agents/config/paulsha/model-identities.yaml` 決定**。workflow 路徑的 launcher 直接取 `identity.executor` 與 `identity.model_id`，且沒有 `PSC_MANAGER_MODEL` 這類環境變數可 pin 模型（只有 `PSC_MANAGER_EXECUTOR`，且它只管 executor）。本批次 builder 為 `codex / gpt-5.3-codex-spark`。
- **model id 要用 CLI 接受的完整字串**。本機帳號只吃帶前綴的 `gpt-5.3-codex-spark`；`5.3-codex-spark` 會被回 400 `not supported when using Codex with a ChatGPT account`。派長任務前先用一句可辨識的短 prompt 驗整組 flag，成本遠低於長跑失敗。
- **cortex 套件預設的 canonical planning identity 已失效**（`agy / "Gemini 3.1 Pro (High)"`）：probe 以字面比對 `agy models` 的輸出行，而 agy 現在輸出 kebab id（`gemini-3.1-pro-high`），顯示名不在清單內即回 `model-not-listed`。預設下那是唯一的 planning identity，因此 work-item workflow 永遠停在 `define` / `needs_human`，對外只表現成 `sizing_unavailable: true` 與空的 evidence 目錄。**繞法**：以真實 id 另宣告一個 agy planning identity（`model_id != AGY_MODEL_ID` 才會走通用 `_probe_identity`），並保持 `independence_domain: google` 與 `live_probe: agy-plan-sandbox`。追蹤於 `hamanpaul/paulsha-cortex` 的 planner identity issue。
- **`PSC_PREFLIGHT_CMD` 必須設**，否則 `cortex doctor` 報 FAIL 且 run 走到 verify/ship 會失敗。本機值：`~/.local/share/paulsha-conventions/bin/policy-preflight --repo-visibility private`（cortex 會自動附加 `--pr <N>` 與 `--skip-tests`；`--repo-visibility` 要明給，否則 sanitized env 下 gh 認證拿不到會 fallback `unknown` 並以公開 repo 的最嚴標準判 R-21 fail-closed）。

## 操作紀律

- **planning artifact 一旦被 claim 就鎖定 `baseline_sha256`，之後不得修改**。改了會讓 resume 報 `workflow planning artifact current authority drift`，而該狀態的唯一出口 `abandon` 會使 work item 留在 `blocked` 且沒有 GC 桿可清。需要調整內容時，**新增檔案**而非改動既有產物——新增會改變 authority digest 並產生新的 claim，改動則觸發 drift。
- `cortex work resume` 與 `start` 都要帶 `--payload`（內含 `repo_root`），否則報 `trusted repo registry did not resolve exactly one owner/name root`。
- 重啟 manager／monitor 後 GitHub provider 會進入 awaiting live refresh，此期間 claim 報 `durable GitHub provider authority invalid`，實測約 90–220 秒恢復，重試即可。
- `cortex tick --specs-dir` 只驅動 deck/spec 的 fanout 路徑，**不會**推進 work-item workflow run。
