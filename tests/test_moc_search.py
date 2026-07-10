# tests/test_moc_search.py
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.moc import search
from paulsha_hippo.moc import frontmatter_io as fio


def _slice(root: Path, slice_id: str, project: str, title: str, body: str) -> None:
    path = root / "knowledge" / project / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n"
                    f"title: {title}\ntags: [t]\ncaptured_at: 2026-06-03T00:00:00Z\n---\n{body}\n",
                    encoding="utf-8")


class SearchTests(unittest.TestCase):
    def test_build_and_query_with_project_scope(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "prplos-core", "flock-ledger", "flock locking on ledger")
            _slice(root, "sl-2", "other", "unrelated", "different content")
            search.build_index(root, link_weights={"sl-1": 3, "sl-2": 0})
            hits = search.search(root, "flock", project="prplos-core", limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-1"])
            self.assertIn("project", hits[0])

    def test_missing_index_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(search.SearchIndexError):
                search.search(Path(tmp), "x", project=None, limit=5, include_decayed=False)

    def test_build_index_failure_preserves_existing_db(self):
        # #16：建索引中途失敗，舊 DB 必須完整保留（廢除先 unlink）
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            before = search.search(root, "alpha", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in before], ["sl-1"])

            _slice(root, "sl-2", "proj", "beta", "beta body")
            with mock.patch(
                "paulsha_hippo.moc.search.retrieval_set.active_records",
                side_effect=RuntimeError("boom mid-build"),
            ), self.assertRaises(RuntimeError):
                search.build_index(root, link_weights={})

            after = search.search(root, "alpha", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in after], ["sl-1"])  # 舊 DB 未損毀
            self.assertFalse((search.index_path(root).parent / "retrieval.db.tmp").exists())

    def test_build_index_success_replaces_db_atomically(self):
        # 守護測試（舊實作下也綠）：成功重建後新內容可查、tmp 不殘留
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            _slice(root, "sl-2", "proj", "beta", "beta body")
            search.build_index(root, link_weights={})
            hits = search.search(root, "beta", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-2"])
            self.assertFalse((search.index_path(root).parent / "retrieval.db.tmp").exists())

    def test_build_index_returns_six_column_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            k.mkdir(parents=True)
            _slice(root, "sl-good", "proj", "good-note", "真實 知識 內容")
            (k / "rev.md").write_text(
                "---\nmemory_layer: knowledge\nslice_id: sl-rev\nproject: proj\n"
                "title: PR Review\nartifact_kind: review\n---\nreview body\n",
                encoding="utf-8")
            (k / "echo.md").write_text(
                "---\nmemory_layer: knowledge\nslice_id: sl-echo\nproject: proj\n"
                "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n",
                encoding="utf-8")
            (k / "badyaml.md").write_text("---\ntitle: [unclosed\n---\nbody\n", encoding="utf-8")
            (k / "nosid.md").write_text(
                "---\nmemory_layer: knowledge\nproject: proj\ntitle: t\n---\nbody\n",
                encoding="utf-8")
            (root / "knowledge" / "wiki-moc.md").write_text(
                "---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n", encoding="utf-8")

            report = search.build_index(root, link_weights={})

            self.assertEqual(report["scanned"], 6)
            self.assertEqual(report["invalid_frontmatter"], 2)  # badyaml + nosid
            self.assertEqual(report["pool_excluded"],
                             {"non-knowledge-layer:moc": 1, "review-record": 1})
            self.assertEqual(report["noise_excluded"], {"structural-echo:CWD": 1})
            self.assertEqual(report["eligible"], 1)
            self.assertEqual(report["indexed"], 1)

    def test_build_index_persists_coverage_json_atomically(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            report = search.build_index(root, link_weights={})
            cov_path = search.coverage_path(root)
            self.assertTrue(cov_path.exists())
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(cov),
                {"scanned", "invalid_frontmatter", "pool_excluded",
                 "noise_excluded", "eligible", "indexed"})
            self.assertEqual(cov["eligible"], cov["indexed"])
            self.assertEqual(cov["indexed"], report["indexed"])

    def test_build_index_report_keeps_per_project_and_warnings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            report = search.build_index(root, link_weights={})
            self.assertEqual(report["per_project"]["proj"],
                             {"indexed": 1, "excluded": 0, "exclude_rate": 0.0})
            self.assertEqual(report["warnings"], [])

    def test_build_index_batches_active_record_lookups(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(205):
                _slice(root, f"sl-{index:03d}", "proj", f"title-{index:03d}", f"body {index}")

            batch_sizes: list[int] = []
            lifecycle_events = [{"record_id": "sl-000", "event_type": "created"}]
            seen_events: list[object] = []

            def fake_active_records(
                memory_root: Path,
                record_ids: list[str],
                *,
                events=None,
            ) -> list[str]:
                batch_sizes.append(len(record_ids))
                seen_events.append(events)
                return record_ids

            with (
                mock.patch(
                    "paulsha_hippo.moc.search.lifecycle.read_events",
                    return_value=lifecycle_events,
                ) as read_events,
                mock.patch(
                    "paulsha_hippo.moc.search.retrieval_set.active_records",
                    side_effect=fake_active_records,
                ),
            ):
                search.build_index(root, link_weights={})

            read_events.assert_called_once_with(root)
            self.assertGreater(len(batch_sizes), 1)
            self.assertTrue(all(size <= 100 for size in batch_sizes), batch_sizes)
            self.assertEqual(sum(batch_sizes), 205)
            self.assertTrue(all(events is lifecycle_events for events in seen_events))

    def test_build_index_matches_legacy_row_contents(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            _slice(root, "sl-2", "proj", "beta", "beta body")
            path = root / "knowledge" / "proj" / "ignored.md"
            path.write_text("---\nmemory_layer: inbox\nproject: proj\ntitle: ignored\n---\nignore\n", encoding="utf-8")
            expected = self._legacy_rows(root, active_ids={"sl-2"}, link_weights={"sl-1": 4, "sl-2": 0})

            with mock.patch(
                "paulsha_hippo.moc.search.retrieval_set.active_records",
                side_effect=lambda memory_root, record_ids, *, events=None: [
                    rid for rid in record_ids if rid == "sl-2"
                ],
            ):
                search.build_index(root, link_weights={"sl-1": 4, "sl-2": 0})

            conn = sqlite3.connect(search.index_path(root))
            try:
                rows = conn.execute(
                    "SELECT slice_id, project, captured_at, active, link_weight FROM slice_meta ORDER BY slice_id"
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(rows, expected)

    def _legacy_rows(
        self,
        root: Path,
        *,
        active_ids: set[str],
        link_weights: dict[str, int],
    ) -> list[tuple[str, str, str, int, int]]:
        rows: list[tuple[str, str, str, int, int]] = []
        for fpath in sorted((root / "knowledge").rglob("*.md")):
            fm, body = fio.read(fpath.read_text(encoding="utf-8"))
            if fm.get("memory_layer") != "knowledge":
                continue
            sid = fm.get("slice_id")
            if not sid:
                continue
            rows.append(
                (
                    str(sid),
                    str(fm.get("project", "")),
                    str(fm.get("captured_at", "")),
                    1 if str(sid) in active_ids else 0,
                    link_weights.get(str(sid), 0),
                )
            )
        return rows


def test_build_index_and_search_return_path(tmp_path):
    from paulsha_hippo.moc import search as S
    mr = tmp_path
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    note = k / "serialwrap.md"
    note.write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\n"
        "project: proj\ntitle: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n"
        "SerialWrap 執行抽象設計\n", encoding="utf-8")
    S.build_index(mr, link_weights={})
    hits = S.search(mr, '"SerialWrap"', project="proj", limit=5, include_decayed=True)
    assert hits and hits[0]["slice_id"] == "sl-aaaaaaaaaaaaaaaa"
    assert hits[0]["path"] == str(note)


def test_build_index_excludes_noise_and_pool(tmp_path):
    from paulsha_hippo.moc import search as S
    from paulsha_hippo.noise import build_corpus
    mr = tmp_path
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True)
    # clean note
    (k / "good.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-good00000000000\nproject: proj\n"
        "title: Good\nartifact_kind: spec\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n真實 知識 內容\n",
        encoding="utf-8")
    # review-record (pool-excluded)
    (k / "rev.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-rev000000000000\nproject: proj\n"
        "title: PR Review\nartifact_kind: review\ncaptured_at: '2026-06-29T00:00:00Z'\n---\nreview body\n",
        encoding="utf-8")
    # structural-echo noise (classify_noise)
    (k / "echo.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-echo00000000000\nproject: proj\n"
        "title: X\nartifact_kind: report\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n## CWD\n/tmp\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={}, doc_corpus=build_corpus([]))
    ids = {h["slice_id"] for h in S.search(mr, '"知識" OR "review" OR "CWD"',
                                           project="proj", limit=10, include_decayed=True)}
    assert "sl-good00000000000" in ids
    assert "sl-rev000000000000" not in ids   # pool-excluded
    assert "sl-echo00000000000" not in ids    # classify_noise


def test_build_index_excludes_generic_title(tmp_path):
    from paulsha_hippo.moc import search as S

    mr = tmp_path
    generic_title = "Report Testpilot"
    generic_sid = "sl-generic0000000"
    concrete_title = "uart-pinmux-diagnosis"
    concrete_sid = "sl-concrete000000"

    _slice(mr, generic_sid, "proj", generic_title, "shared retrieval signal")
    _slice(mr, concrete_sid, "proj", concrete_title, "shared retrieval signal")

    S.build_index(mr, link_weights={})

    hits = S.search(mr, "retrieval", project="proj", limit=10, include_decayed=True)
    assert [h["slice_id"] for h in hits] == [concrete_sid]
    assert (mr / "knowledge" / "proj" / f"{generic_title}--{generic_sid}.md").exists()


if __name__ == "__main__":
    unittest.main()
