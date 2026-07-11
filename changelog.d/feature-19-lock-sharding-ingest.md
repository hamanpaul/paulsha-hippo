### Changed
- importer ingest（#19）改用 64-shard session lock，`runtime/locks/` 不再產生 legacy per-session `{safe_key(key)}.lock`，僅保留 shard lock 與既有 shared locks。
