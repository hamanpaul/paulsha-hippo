### Fixed

- `moc.search.search()` 排序鍵改為 `(adjusted_score, base_score, slice_id)` 三段式穩定鍵；先前僅以單一 `bm25 - 0.1*link_weight` 鍵排序，usage boost 造成同分時退回插入序而非以 `base_score` 決勝，違反 issue #41 v5 排序不變式（BLOCKER #7）。
