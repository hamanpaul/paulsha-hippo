### Changed
- `hippo doctor` 新增 runtime 健康報告：global dream lock（`runtime/locks/dream.lock`）持鎖狀態＋dream/supervise 進程清單（PID/start time/cmdline/cwd），標記非 canonical 實例（interpreter-mismatch／cwd-missing／cwd-temp-worktree）；只報告，不自動 kill（#19）
