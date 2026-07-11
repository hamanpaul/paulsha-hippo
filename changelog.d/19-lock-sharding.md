### Added
- `hippo locks cleanup-legacy --memory-root <root> [--apply]`：legacy per-session lock 檔一次性清理；預設 dry-run，apply 受雙層安全閘保護（偵測到其他 hippo 進程即拒絕＋逐檔 flock 探測跳過 busy），僅供恢復序列維護窗口使用（#19）
