---
type: change
scope: release
---
### Changed

- 版號 `0.1.2` → `1.0.0`（MAJOR bump，repo owner 於 2026-09-26 明確核可）：`VERSION`、`pyproject.toml`、`paulsha_hippo.__version__`、`paulsha_hippo.importer.__version__`、`scripts/build_release_artifact.py` 的 manifest 預設值，以及斷言真實版號的測試（`test_build_info` 含嵌入式 `_build.json` fixture、`test_cli` 的 `--version` 輸出、`test_ops` 產生的 systemd unit `HIPPO_BUILD_VERSION`、`tests/installed` 乾淨安裝的 version JSON）。`test_task_memory_payload_contract` 的 `producer.version` 是輸入 fixture、不是版本宣告，刻意不動。
- `CHANGELOG.md` 的 `[Unreleased]` 定稿為 `[1.0.0] - 2026-09-27`，沿用既有凍結作法（保留空 `[Unreleased]` 於頂、`changelog.d/` 碎片不清除）。
- 1.0.0 是 release candidate：`reports/verify/release-readiness-matrix.json` 仍綁在 0.1.2，本版的 gate 一律從 pending 起算；candidate 先部署到本機測試並跑 soak，gate 全數 attest 後才打 `v1.0.0` tag。狀態見 `docs/release-readiness.md` 的 1.0.0 段。
