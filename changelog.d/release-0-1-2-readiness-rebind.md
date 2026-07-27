---
type: chore
scope: release
---
### Changed

- `reports/verify/release-readiness-matrix.json` 由 v0.1.1 candidate（`eb2ccb86…`）重綁至 0.1.2 candidate（main HEAD `c147218353bdc1e06f6a2f1cbc59a3a61130ceb6`）；`wheel_sha256` 改為 `null`（0.1.2 wheel 尚未建置）。16 個 gate 中僅 AR-01（全套測試）在本 worktree 實跑 `python3 -m pytest -q` 並用真實輸出 attest 為 `passed`（1634 passed, 4 skipped, 154 subtests passed, 2 failed——2 個失敗為已知巢狀 worktree 環境假訊號，evidence 內誠實註明，非全綠）；其餘 15 個 gate（AR-02～AR-14、IC-01、IC-02）皆誠實改列 `state: "pending"`，evidence 改寫為「0.1.2 待執行」並說明各自所需證據，`rerun` 保留/更新重跑指令，`timestamp` 清空。
- `docs/release-readiness.md` 新增版本無關的總覽段與獨立「0.1.2 release candidate readiness」小節，說明目前綁定的 candidate 與 matrix 待重跑狀態；保留原「0.1.1 release candidate readiness」歷史記載不變。
- `tests/test_readiness_matrix.py` 的既有測試原本硬編碼 v0.1.1 candidate commit／wheel hash 並斷言「全部 gate 皆 passed」，隨 matrix 重綁此斷言必然失敗；改為斷言與候選版本無關的結構性不變量（`load_matrix` schema 有效、candidate commit 形似合法 git SHA、任何 `passed` gate 都帶 evidence、每個 gate 都保留 rerun 指令），不再綁死特定 release 的字面值。
