---
type: chore
---
### Changed

- `docs/release-readiness.md` 補上 2026-08-07 的 current-state 交接段，並把既有內容明確標記為 history。此前該文件的敘述已落後於 matrix 本身（文中稱 `wheel_sha256` 為 `null`，實際已綁 `b942feb2…`），讀者無從得知哪些敘述仍成立。
- 記錄三件對發版判讀有決定性影響、原本只存在於 session 脈絡的事實：**(1) 發版凍結生效**，`main` 凍於 `4d2b1f2`（#121），0.1.3 才收新的 `code_paths` 變更；**(2) matrix binding 是刻意保留的 stale**——仍綁 `f5df394`，其後已落地 14 個 commit，依 `readiness.bind_candidate()` 的漂移規則，該檔內含 13 個 `passed` 的 attestation 全數已成 zombie evidence，rebind 刻意延後到 soak 完成後一次付清；**(3) AR-11 soak 因換版自 `2026-08-07T05:00:05Z` 歸零重算（0/3）**，`1299fa1` 在 `08-04`→`08-06` 窗口達成的 3/3 不可轉移。
- 補記完整部署的**四個獨立同步點**（先前僅 pipx 一項見於文件）：package 本體、live config（`deadline_seconds` 必須恰等於 `FIXED_SESSION_DEADLINE_SECONDS` 否則每輪 fail-closed；cg `max_session_chunks` 因使用者值勝出不會被模板刷新）、`~/.local/bin/local-vllm`（wheel 外的獨立副本，本次部署前仍是 pre-#110 版本）、`hippo install hooks`（hook 腳本亦綁 build commit，`pipx install` 不涵蓋，只有 `hippo doctor` 抓得到）。
- 補記 `02:00Z` 為 partial 熱點（07-31／08-03／08-04／08-06 各一次 `parked` +1，皆為降級鏈耗盡），以及 #110／#112／#121 首次部署後該時段是否停止 park 即為三者是否有效的第一個訊號。
- 交接工具 `~/.local/share/hippo-ops/soak-check.py`（本機，非 repo 產物）可於任何時間由 append-only ledger 重現窗口分析。
- 本次僅改文件，未改 `reports/verify/release-readiness-matrix.json`——rebind 屬實質發版動作，留待 soak 達標後與 gate 重跑一併執行。
