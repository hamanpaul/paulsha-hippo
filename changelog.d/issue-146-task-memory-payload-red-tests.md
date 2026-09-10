### Added

- #146：新增通用 task memory payload RED 契約回歸測試，固定 bounded candidates、授權/可用性結果、redaction，以及 inline/snapshot/note_fetch 的 read KPI 語意。

### Fixed

- #146：將 task memory payload truncation fixture 擴成四筆 authorized 候選，明確驗證 deterministic first-three selection，而非讓 `MAX_CANDIDATES=3` 落在三選三 no-op。
