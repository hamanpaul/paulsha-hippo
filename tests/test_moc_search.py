# tests/test_moc_search.py
from __future__ import annotations

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
