### Added
- `hippo recall` CLI（#17）：新增跨 CLI consumer API 的 shortlist 入口，接受 `--memory-root/--cwd/--prompt/--tool/--session-id`，stdout 輸出 shortlist 區塊，並沿用 offered ledger 與 per-session offered map 的 `tool` 歸因記錄。
