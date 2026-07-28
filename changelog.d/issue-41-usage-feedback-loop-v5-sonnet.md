---
type: fix
---
- `moc.search.search()` 有 boost 時使用 `(adjusted_score, base_score, slice_id)` 穩定鍵；無有效 boost（含舊 schema fallback）維持 legacy base-score one-key stable ordering。
- usage ledger 改用 binary per-line UTF-8 streaming；metadata/open/read、decode、parse error fail-soft，壞行不吞合法鄰行，CLI 不保留 ledger-wide raw row lists。
- offered/read 的 malformed、future/out-of-window 與 `(unknown)` tool/session/sl_id 不得 cross-match，統一記固定鍵 bounded diagnostics。

### Added

- index build 新增 keyword-only UTC `usage_now`／`usage_window_days` 注入，預設窗口 30 天；usage boost 上限 0.04。
- janitor retention 使用 valid `last_read_at`，future read 不延長 TTL；`superseded`／`source_invalid` 優先序、read 不 reactivation 與 scanner positive-only warning 維持不變。
