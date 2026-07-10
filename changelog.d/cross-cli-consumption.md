### Added
- `hippo recall`：跨 CLI consumer API——以 prompt 檢索任務相關 shortlist 並記 `offered`（含 `--tool` 歸因），供無 prompt-time hook 的平台顯式呼叫（#17）。
- `hippo usage mark-applied`：applied 顯式訊號（agent structured acknowledgement）——ledger 事件 `{"kind":"applied","session_id","slice_id","tool","ts"}`；寫入前反查 `offered.jsonl` 做參照完整性驗證（無對應 offer 即拒寫，防偽造事件污染漏斗遙測）；shortlist 尾行注入回報指引（#18）。
- `hippo usage` 報表新增 per-tool `offered / read / applied` 分列；`applied` 無訊號時顯示 `n/a`，不做內容 substring 猜測（#18）。
- codex / copilot SessionStart 改注入顯式 recall 指引（不假裝 orientation 等同 task retrieval）；跨 CLI 能力矩陣（官方文件＋本機 probe 實測）落 `docs/cross-cli-capability-matrix.md`（#17）。

### Fixed
- 文件漂移：`hippo usage` 實際同時讀 `offered.jsonl` 與 `memory_usage.jsonl`，openspec telemetry spec 誤稱「僅讀 memory_usage.jsonl」——以實作為準修正（含 `psc memory`→`hippo`、`paulshaclaw/memory/usage.py`→`paulsha_hippo/usage.py` 命名殘留）（#18）。
