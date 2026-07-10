### Added
- `hippo usage mark-applied`（#18）：新增 applied 顯式訊號 CLI，僅允許回報同 `(session_id, tool)` 已被 offered 的 `slice_id`，拒絕偽造事件並將合法 applied 事件寫入 usage ledger。
- shortlist 回報指引（#18）：在提示注入尾端附上 `usage mark-applied` 可貼命令，讓 agent 能以完整 session/tool 歸因回報哪條記憶實際影響了做法。
