---
type: change
scope: release
---
### Changed

- `v1.0.0` 改為比照 0.1.2，打在 release candidate commit `f75ffa1a`（annotated tag）。
  - 16 個 release gate 的證據（soak、attest）於 tag 之後以 post-tag evidence commit 補齊；GitHub release 等全部 gate 通過才發布。
  - 原因：policy R-07 要求 VERSION 與最新 tag 一致，未打 tag 的 MAJOR candidate 會擋住之後所有 PR。Paul 2026-09-27 裁決。
  - `docs/release-readiness.md` 的 1.0.0 段落已同步更新。
