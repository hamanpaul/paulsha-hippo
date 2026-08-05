from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.lib.lifecycle.schema import compute_checksum, validate_frontmatter
from paulsha_hippo.moc import census as moc_census
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.moc import search as moc_search

_SLICE = (
    "---\n"
    "phase: research\nproject: paulshaclaw\nslice_id: sl-abc\nartifact_kind: research\n"
    "version: 1\ncreated_at: 2026-06-03T00:00:00Z\ncreated_by: claude\n"
    "source_session: s1\ngate_required: false\nchecksum: __CK__\n"
    "memory_layer: knowledge\nsource_agent: claude\ncaptured_at: 2026-06-03T00:00:00Z\n"
    "supersedes: []\ndistilled_from: claude:s1\n"
    "provenance:\n  repo: r\n  commit: c\n  path: p\n"
    "---\n"
    "BODY LINE ONE\nBODY LINE TWO\n"
)


def _slice_text() -> str:
    body = "BODY LINE ONE\nBODY LINE TWO\n"
    return _SLICE.replace("__CK__", compute_checksum(body))


class FrontmatterIoTests(unittest.TestCase):
    def test_read_splits_frontmatter_and_body(self):
        fm, body = fio.read(_slice_text())
        self.assertEqual(fm["slice_id"], "sl-abc")
        self.assertEqual(body, "BODY LINE ONE\nBODY LINE TWO\n")

    def test_rewrite_preserves_body_and_checksum(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.md"
            path.write_text(_slice_text(), encoding="utf-8")
            fio.update(path, {"title": "Alpha", "aliases": ["Alpha"],
                              "related": ["[[Beta--sl-2]]", "[[MTK]]"]})
            fm, body = fio.read(path.read_text(encoding="utf-8"))
            self.assertEqual(fm["title"], "Alpha")
            self.assertEqual(fm["related"], ["[[Beta--sl-2]]", "[[MTK]]"])
            # body and checksum unchanged -> Stage 3 still validates
            self.assertEqual(body, "BODY LINE ONE\nBODY LINE TWO\n")
            result = validate_frontmatter(frontmatter=fm, body=body)
            self.assertTrue(result.ok, result.errors)


class ScalarQuotingRoundTripTests(unittest.TestCase):
    """Issue #102: ``_scalar()`` 對「YAML round-trip 會變成非字串」的字串樣值
    （數字樣／布林樣／null 樣／空字串）不加引號，導致 ``update()`` 對既有
    frontmatter 做無關欄位更新時，重新 ``dump()`` 會把這些字串值剝除引號、
    下一次 ``read()`` 被 YAML 解析回原生 int/bool/None 型別。

    生產劇本：issue #101 熱修把 tag ``264``（int）改成 ``"264"``（str）存活
    不到一輪——janitor/moc pass 對同一份 frontmatter 呼叫
    ``frontmatter_io.update()`` 更新其他欄位時，經 ``_scalar()`` 序列化又把
    引號剝掉，下一輪 MOC index 的嚴格 tags 驗證再次判 invalid。
    """

    # 判準（比清單更可靠）：yaml.safe_load(candidate) 的型別 != str 即需引號。
    ROUND_TRIP_HAZARDS = [
        "264", "1.5", "1e3", "true", "True", "false", "no", "off",
        "null", "~", "", "normal string", "中文標籤",
    ]

    def test_scalar_field_round_trips_as_str_across_two_update_cycles(self):
        for value in self.ROUND_TRIP_HAZARDS:
            with self.subTest(value=value):
                with TemporaryDirectory() as tmp:
                    path = Path(tmp) / "s.md"
                    path.write_text(_slice_text(), encoding="utf-8")

                    # write (1st update) -> read
                    fio.update(path, {"probe": value})
                    fm1, _ = fio.read(path.read_text(encoding="utf-8"))
                    self.assertIsInstance(fm1["probe"], str, f"{value!r} lost str type on 1st round-trip")
                    self.assertEqual(fm1["probe"], value)

                    # write (2nd update, unrelated field forces a full re-dump) -> read
                    fio.update(path, {"title": "unrelated change"})
                    fm2, _ = fio.read(path.read_text(encoding="utf-8"))
                    self.assertIsInstance(fm2["probe"], str, f"{value!r} lost str type on 2nd round-trip")
                    self.assertEqual(fm2["probe"], value)

    def test_list_element_round_trips_as_str_across_two_update_cycles(self):
        for value in self.ROUND_TRIP_HAZARDS:
            with self.subTest(value=value):
                with TemporaryDirectory() as tmp:
                    path = Path(tmp) / "s.md"
                    path.write_text(_slice_text(), encoding="utf-8")

                    fio.update(path, {"tags": [value]})
                    fm1, _ = fio.read(path.read_text(encoding="utf-8"))
                    self.assertEqual(fm1["tags"], [value])
                    self.assertTrue(all(isinstance(t, str) for t in fm1["tags"]))

                    fio.update(path, {"title": "unrelated change"})
                    fm2, _ = fio.read(path.read_text(encoding="utf-8"))
                    self.assertEqual(fm2["tags"], [value])
                    self.assertTrue(all(isinstance(t, str) for t in fm2["tags"]))


class NullFidelityRoundTripTests(unittest.TestCase):
    """Issue #109 review：``_scalar(None)`` 曾輸出 ``None`` 字面值，但 PyYAML
    不把 ``None`` 當 null 詞彙，``update()`` 往返後原生 null 劣化成字串
    "None"（實質資料劣化）。修正為輸出 ``null``，比照 #102/#104 的型別保真
    精神；同時與既有 ROUND_TRIP_HAZARDS 的字串 ``"null"``（維持引號字串）
    方向相反、互不干擾。"""

    def test_scalar_none_emits_yaml_null_literal(self):
        self.assertEqual(fio._scalar(None), "null")

    def test_null_values_survive_repeated_update_round_trips(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.md"
            text = _slice_text().replace(
                "distilled_from: claude:s1\n",
                "distilled_from: claude:s1\n"
                "fallback_note: null\n"
                "distiller:\n  profile_id: cg\n  fallback_reason: null\n",
            )
            path.write_text(text, encoding="utf-8")
            fm0, _ = fio.read(path.read_text(encoding="utf-8"))
            self.assertIsNone(fm0["fallback_note"])
            self.assertIsNone(fm0["distiller"]["fallback_reason"])

            # 多輪無關欄位 update（janitor/moc 每輪 rewrite 劇本）後 null 不漂移
            for i in range(2):
                fio.update(path, {"title": f"pass {i}"})
                fm, _ = fio.read(path.read_text(encoding="utf-8"))
                self.assertIsNone(fm["fallback_note"], "top-level null 劣化成字串 'None'")
                self.assertIsNone(fm["distiller"]["fallback_reason"], "nested null 劣化成字串 'None'")
            self.assertIn("fallback_reason: null", path.read_text(encoding="utf-8"))


class ProductionTagQuotingScenarioTests(unittest.TestCase):
    """整合測試：重現 issue #101 熱修（tag ``264`` -> ``"264"``）在下一輪
    ``frontmatter_io.update()`` 後被剝除引號、令 MOC index 的嚴格 tags
    驗證（``moc/search.py::_tags_fts_text``、``moc/census.py::
    _census_tags_invalid``）重新判 invalid_frontmatter 的生產劇本。
    """

    def _slice_with_tag_264(self) -> str:
        body = "BODY LINE ONE\nBODY LINE TWO\n"
        text = _SLICE.replace("__CK__", compute_checksum(body))
        # 已由主線程熱修過的狀態：tag 已是帶引號的字串 "264"。
        return text.replace(
            "distilled_from: claude:s1\n",
            'distilled_from: claude:s1\ntags:\n  - "264"\n',
        )

    def test_tags_survive_unrelated_update_and_pass_moc_validation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.md"
            path.write_text(self._slice_with_tag_264(), encoding="utf-8")

            fm0, _ = fio.read(path.read_text(encoding="utf-8"))
            self.assertEqual(fm0["tags"], ["264"])

            # A janitor/moc pass rewriting an unrelated field (title) must not
            # disturb the already-hotfixed tags value.
            fio.update(path, {"title": "retitled by pass"})

            fm1, _ = fio.read(path.read_text(encoding="utf-8"))
            self.assertEqual(fm1["tags"], ["264"])
            self.assertTrue(all(isinstance(t, str) for t in fm1["tags"]))
            self.assertEqual(fm1["title"], "retitled by pass")

            # Cross-check against the actual MOC index tags validators (#101).
            self.assertIsNotNone(moc_search._tags_fts_text(fm1["tags"]))
            self.assertFalse(moc_census._census_tags_invalid(fm1["tags"]))

    def test_repeated_passes_keep_tags_valid(self):
        # janitor/moc rewrites frontmatter every cycle; simulate several
        # consecutive unrelated-field updates (the "每輪 rewrite" scenario).
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.md"
            path.write_text(self._slice_with_tag_264(), encoding="utf-8")

            for i in range(3):
                fio.update(path, {"title": f"pass {i}"})
                fm, _ = fio.read(path.read_text(encoding="utf-8"))
                self.assertEqual(fm["tags"], ["264"])
                self.assertIsNotNone(moc_search._tags_fts_text(fm["tags"]))
                self.assertFalse(moc_census._census_tags_invalid(fm["tags"]))


if __name__ == "__main__":
    unittest.main()
