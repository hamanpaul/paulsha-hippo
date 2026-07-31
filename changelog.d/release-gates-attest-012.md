---
type: chore
---
### Changed

- 0.1.2 candidate（`f5df394`）release gate 全面 attest：AR-02/03/04/06/07/08/09/10/12/13 與 IC-01/02 共 13 gate 轉 `passed`，逐項證據（wheel/manifest 雜湊、乾淨 venv 身分綁定、migration 三次 hash 一致、生產 timer 輪的完整降級鏈發布、86/86 與 80/80 測試家族、三客戶端 hook ledger、隔離 staged upgrade fail-closed、迷你 root recovery 全鏈＋live 唯讀 index census 1058/1058、policy/openspec/diff 驗證、batch closeout report）記入 readiness matrix；`wheel_sha256` 綁定 `b942feb2…`。餘 AR-05（codex provider quota，2026-08-05 重置後重跑）、AR-11（soak 1/3，自然累積）、AR-14（維護者暫緩）。新增 `reports/verify/release-0.1.2-closeout.md`（17 issue batch closeout，明列 issue 63 not-planned 例外與 3 個未歸檔 openspec change）。附帶捕獲 follow-up：`hippo search` 對現行索引報 `no such column: build`；`build_release_artifact.py` version 欄與 pyproject 脫鉤。
