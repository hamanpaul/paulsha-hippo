### Changed
- importer per-session lock 改為固定 64 個 hash-sharded locks（`lock_shard_{h:02x}.lock`，`h = crc32(safe_key(key)) % 64`）：`runtime/locks/` 檔案數收斂為常數上界，碰撞只降低並行度、不影響互斥正確性（#19）
