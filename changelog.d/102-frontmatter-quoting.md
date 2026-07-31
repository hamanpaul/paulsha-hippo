---
type: fix
---
- 修 issue #102：`moc/frontmatter_io.py::_scalar()` 對數字樣／布林樣／null 樣字串不加引號，`update()`/rewrite 往返會把已加引號的字串值剝回 YAML 原生型別——issue #101 的資料熱修（tag `264`（int）改成 `"264"`（str））因此存活不到一輪 dream cycle：janitor/moc pass 每輪對上千檔 frontmatter 呼叫 `update()` 更新無關欄位（如 title）時，經 `_scalar()` 重新序列化就把引號剝除，下一輪 MOC index 的嚴格 tags 驗證（`moc/search.py::_tags_fts_text`、`moc/census.py::_census_tags_invalid`）再度判 `invalid_frontmatter`，sticky partial 復發。資料層熱修對此 bug 無效，唯 code fix 能終結。
- `_scalar()` 新增 `_needs_quoting_for_type_fidelity()`：判準用實測不用清單——`yaml.safe_load(candidate)` 的型別 != `str` 即需引號，一網打盡數字樣（`264`/`1.5`/`1e3`）、布林樣（`true`/`no`/`off`）、null 樣（`null`/`~`）、空字串等所有 YAML 隱式型別轉換。只套用在真正的 `str` 值上（`isinstance(value, str)` 為前提），不影響既有對非字串值（如 datetime）的既有 escaping 行為，維持與現有測試相容；引號形式沿用既有雙引號＋跳脫慣例（#139）。
- 通用保真修復：不只 tags，`dump()`/`_emit()` 路徑下所有 list/scalar 欄位的字串值都受保護（數字樣 title/alias 同理免於型別漂移）。
- 新增往返測試（write→read→write→read，涵蓋 `264`/`1.5`/`true`/`null`/`no`/空字串/正常字串/中文）與生產劇本整合測試（tags 含 `"264"` 的 slice 檔經 `frontmatter_io.update()` 更新無關欄位後，tags 仍全為 `str` 且通過 `moc/search.py`／`moc/census.py` 的嚴格驗證）。
