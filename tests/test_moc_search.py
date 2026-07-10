# tests/test_moc_search.py
from __future__ import annotations

import json
import sqlite3
import threading
import time
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

    def test_build_index_cleans_stale_tmp_leftovers(self):
        # crash 殘留的半成品（舊固定名與新唯一名皆有可能）：持鎖重建時清掉
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            indexes = search.index_path(root).parent
            indexes.mkdir(parents=True)
            (indexes / "retrieval.db.tmp").write_text("stale legacy", encoding="utf-8")
            (indexes / "retrieval.db.999-dead0000.tmp").write_text("stale crash", encoding="utf-8")
            (indexes / "retrieval.coverage.json.999-dead0000.tmp").write_text("{}", encoding="utf-8")
            search.build_index(root, link_weights={})
            self.assertEqual(list(indexes.glob("*.tmp")), [])
            hits = search.search(root, "alpha", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-1"])

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


class AtomicCoveragePublishTests(unittest.TestCase):
    """索引與 coverage 必須由單次 os.replace 原子發布（Codex 複驗 blocking）。

    舊實作先 replace 新 DB、之後才寫 coverage JSON：coverage 寫入失敗
    （ENOSPC/OSError）或程序在兩步間終止時，build_index 回報失敗但舊 DB
    已被不可逆替換——留下新 DB＋舊/缺 coverage，違反「建索引失敗時舊 DB
    完整保留」驗收契約。修正：coverage 併入同一顆 temp DB（coverage 表），
    replace 之前任何失敗都只丟 temp 檔；coverage JSON 改為發布成功後的
    派生輸出，衍生失敗僅記 warning、不推翻已原子發布的索引。
    """

    def _built_with_baseline(self, tmp: str) -> "tuple[Path, str]":
        root = Path(tmp)
        _slice(root, "sl-1", "proj", "alpha", "alpha body")
        search.build_index(root, link_weights={})
        old_cov = search.coverage_path(root).read_text(encoding="utf-8")
        _slice(root, "sl-2", "proj", "beta", "beta body")
        return root, old_cov

    def test_coverage_merge_failure_preserves_old_db_and_coverage(self):
        # (a) coverage 合併進 temp DB 階段失敗 → 舊 DB 與舊 coverage 完整保留
        with TemporaryDirectory() as tmp:
            root, old_cov = self._built_with_baseline(tmp)
            with mock.patch(
                "paulsha_hippo.moc.search._persist_coverage_table",
                side_effect=OSError(28, "No space left on device"),
            ), self.assertRaises(OSError):
                search.build_index(root, link_weights={})

            hits = search.search(root, "alpha OR beta", project=None, limit=10,
                                 include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-1"])  # 舊 DB 未被替換
            self.assertEqual(
                search.coverage_path(root).read_text(encoding="utf-8"), old_cov)
            self.assertEqual(list(search.index_path(root).parent.glob("*.tmp")), [])

    def test_coverage_json_derivation_failure_keeps_index_published(self):
        # (b) replace 成功後派生 JSON 失敗 → 索引可用、不回報整體失敗、僅記 warning
        with TemporaryDirectory() as tmp:
            root, old_cov = self._built_with_baseline(tmp)
            with mock.patch(
                "paulsha_hippo.moc.search._write_coverage",
                side_effect=OSError(28, "No space left on device"),
            ):
                report = search.build_index(root, link_weights={})

            self.assertEqual(report["indexed"], 2)
            self.assertTrue(any("coverage json" in w for w in report["warnings"]))
            ids = {h["slice_id"] for h in search.search(
                root, "alpha OR beta", project=None, limit=10, include_decayed=True)}
            self.assertEqual(ids, {"sl-1", "sl-2"})  # 新索引已原子發布可用
            # 派生 JSON 停在舊版：讀取端以 DB 內權威 coverage 為準
            self.assertEqual(
                search.coverage_path(root).read_text(encoding="utf-8"), old_cov)
            self.assertEqual(search.read_coverage(root)["indexed"], 2)

    def test_read_coverage_matches_derived_json_after_success(self):
        # 權威（DB coverage 表）與派生（JSON）在成功發布後內容一致、恰六鍵
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            from_db = search.read_coverage(root)
            from_json = json.loads(
                search.coverage_path(root).read_text(encoding="utf-8"))
            self.assertEqual(from_db, from_json)
            self.assertEqual(
                set(from_db),
                {"scanned", "invalid_frontmatter", "pool_excluded",
                 "noise_excluded", "eligible", "indexed"})

    def test_read_coverage_absent_index_or_legacy_db_returns_none(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(search.read_coverage(root))  # 索引不存在
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DROP TABLE coverage")  # 模擬本表出現前的舊版 DB
            conn.commit()
            conn.close()
            self.assertIsNone(search.read_coverage(root))


class ConcurrentRebuildTests(unittest.TestCase):
    """並發重建不得發布半成品（Codex review blocking）。

    舊實作共用固定 temp 路徑：交錯的兩個 writer 可互相 unlink 對方仍開啟
    的 inode，隨後 os.replace 把對方未完成的 DB 發布成正式索引。修正為
    build_index 全程持 index-rebuild.lock（阻塞 flock）互斥 + per-invocation
    唯一 temp 路徑；讀者永遠只看到完整舊版或完整新版。
    """

    def test_concurrent_rebuilds_serialize_and_readers_see_complete_db(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})  # 完整舊版基準
            _slice(root, "sl-2", "proj", "beta", "beta body")

            first_entered = threading.Event()
            release_first = threading.Event()
            calls: list[int] = []
            real_active = search.retrieval_set.active_records

            def gate(memory_root, record_ids, *, events=None):
                calls.append(len(record_ids))
                if len(calls) == 1:  # writer A 卡在 build 主體中段
                    first_entered.set()
                    if not release_first.wait(timeout=10):
                        raise AssertionError("release_first never signalled")
                return real_active(memory_root, record_ids, events=events)

            errors: list[BaseException] = []

            def build():
                try:
                    search.build_index(root, link_weights={})
                except BaseException as exc:  # pragma: no cover - 失敗即測試失敗
                    errors.append(exc)

            with mock.patch("paulsha_hippo.moc.search.retrieval_set.active_records",
                            side_effect=gate):
                writer_a = threading.Thread(target=build)
                writer_a.start()
                self.assertTrue(first_entered.wait(timeout=10))
                writer_b = threading.Thread(target=build)  # 與 A 交錯進場
                writer_b.start()
                time.sleep(0.3)  # 若無鎖，B 在此窗口內早已跑完 build 主體
                # A 持鎖未完成期間：B 不得進入 build 主體，索引仍是完整舊版
                self.assertEqual(len(calls), 1)
                hits = search.search(root, "alpha", project=None, limit=5,
                                     include_decayed=True)
                self.assertEqual([h["slice_id"] for h in hits], ["sl-1"])
                release_first.set()
                writer_a.join(timeout=10)
                writer_b.join(timeout=10)
                self.assertFalse(writer_a.is_alive())
                self.assertFalse(writer_b.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(calls), 2)  # 兩輪 build 序列化各跑一次
            # 兩輪都完成後：完整新版（兩筆皆可搜）、coverage 成對、無 tmp 殘留
            ids = {h["slice_id"] for h in search.search(
                root, "alpha OR beta", project=None, limit=10, include_decayed=True)}
            self.assertEqual(ids, {"sl-1", "sl-2"})
            coverage = json.loads(search.coverage_path(root).read_text(encoding="utf-8"))
            self.assertEqual(coverage["indexed"], 2)
            self.assertEqual(list(search.index_path(root).parent.glob("*.tmp")), [])


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
