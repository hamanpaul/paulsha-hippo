---
type: change
scope: release
---
### Changed

- `reports/verify/release-readiness-matrix.json` 的 0.1.2 candidate 由 `c147218…` 重綁至 main HEAD `2f3dc981e6c283fbe3f85ccc9fbd7cb79c8ae809`。舊綁定在 #75（session 預算解耦）、#76（policy v1.0.15）、#78（tier-1 plan-mode 契約修復）三個 merge 後已漂移，依 `readiness.bind_candidate()` 的漂移語意，舊 candidate 的 `passed` 證據一律作廢——故 **AR-01 不沿用，重新實跑**：兩個獨立環境數字完全一致（GitHub Actions 於該 commit 的 Tests run `1646 passed, 4 skipped, 154 subtests passed in 107.61s`，conclusion=success；本機非巢狀 sibling worktree `python3 -m pytest -q` 得 `1646 passed, 4 skipped, 154 subtests passed, 0 failed in 186.01s`）。`wheel_sha256` 維持 `null`（0.1.2 wheel 尚未建置），其餘 15 個 gate 維持誠實 `pending`。
- AR-05 evidence 更新環境註記：先前記載「本 worktree 未安裝可探測的外部 CLI」已不符現況——`claude` 2.1.220、`codex` 0.145.0、`cg` 均在 `PATH` 且於 2026-07-28 有實際執行紀錄，該 gate 現具備可跑條件，`pending` 僅因尚未對本 candidate 取得 probe 輸出。
- AR-11 evidence 補記修復前後的實測對照：`05:00:42Z` 那輪 timer 將 `claude-code:7d35853a…` park（claude `invalid_output`、606 bytes、86.4s），修復後同一 session 的 chunk 0 以部署中的 runtime 與 live config 重跑得 exit 0、190.5s、15,858 bytes 合法 schema-1 JSON 並通過 `parse_response()`；**soak 仍為 0/3**，且單 chunk 190–244s × 大 session 7 chunks 在 600s chain 預算下仍會 timeout 而 park，故「有 ingress 的 cycle 必然產出 accepted atom」尚不成立。
- AR-11 `rerun` 指令修正判定條件：改以 `passes.atomize.split_sessions > 0`（有全新 ingress session）搭配 `atomize.slices > 0`（有 accepted atom）認定合格 cycle。原指令用 `health.eligible > 0`，但 `health.eligible` 是由 `notes_created` 指派（`dream/orchestrator.py:112`）＝該輪產出的 slice 數，與 `slices` 同義，**不是** ingress 指標，照原條件核對會漏掉「有無新 session 進來」這一半要求。
- `docs/release-readiness.md` 的 0.1.2 小節同步以上四項，並補上重綁歷史（為何舊 candidate 失效、`bind_candidate()` 因 wheel 未建無法直接呼叫故以相同語意手動套用後再過 `load_matrix()` 驗證）與 gate 依賴關係（AR-02/03/07/14 卡在 wheel 未建；AR-05/06/08、IC-01 卡在 wheel 未安裝到乾淨環境）。
