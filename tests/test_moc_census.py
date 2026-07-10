"""三方對賬（#16）：filesystem census × coverage 報表 × index DB 反查。"""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import cli
from paulsha_hippo.moc import census, runner, search
from paulsha_hippo.noise import NoiseVerdict  # 僅供測試注入假 verdict；census 本體不得 import


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_mixed_tree(root: Path) -> None:
    """1 eligible + 1 pool(review) + 1 noise(echo) + 2 invalid（壞 YAML、壞編碼）+ 1 moc。"""
    k = root / "knowledge" / "proj"
    _write(k / "good--sl-good.md",
           "---\nslice_id: sl-good\nmemory_layer: knowledge\nproject: proj\n"
           "title: 索引良品\ntags: [t]\ncaptured_at: 2026-07-10T00:00:00Z\n---\n真實 知識 內容\n")
    _write(k / "rev--sl-rev.md",
           "---\nslice_id: sl-rev\nmemory_layer: knowledge\nproject: proj\n"
           "title: PR Review\nartifact_kind: review\n---\nreview body\n")
    _write(k / "echo--sl-echo.md",
           "---\nslice_id: sl-echo\nmemory_layer: knowledge\nproject: proj\n"
           "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n")
    _write(k / "badyaml.md", "---\ntitle: [unclosed\n---\nbody\n")
    k.joinpath("broken.md").write_bytes(b"---\nslice_id: sl-broken\n\xff\xfe---\nbody\n")
    _write(root / "knowledge" / "wiki-moc.md",
           "---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n")


class CensusTests(unittest.TestCase):
    def test_filesystem_census_enumerates_ids_without_yaml(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            entries = census.filesystem_census(root)
            self.assertEqual(len(entries), 6)
            by_name = {Path(e.path).name: e for e in entries}
            self.assertEqual(by_name["good--sl-good.md"].slice_id, "sl-good")
            self.assertEqual(by_name["good--sl-good.md"].memory_layer, "knowledge")
            self.assertEqual(by_name["wiki-moc.md"].memory_layer, "moc")
            self.assertIsNone(by_name["wiki-moc.md"].slice_id)
            self.assertIsNone(by_name["broken.md"].slice_id)  # 壞編碼 → 讀不到欄位

    def test_reconcile_index_passes_on_consistent_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertEqual(result.problems, [])
            self.assertTrue(result.ok)
            self.assertEqual(result.census_files, 6)
            self.assertEqual(result.eligible_ids, {"sl-good"})
            self.assertEqual(result.indexed_ids, {"sl-good"})

    def test_reconcile_index_detects_missing_indexed_id(self):
        # DB 反查與 coverage 宣稱不一致：模擬索引掉行
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DELETE FROM slice_meta WHERE slice_id = 'sl-good'")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))

    def test_reconcile_index_detects_post_build_disk_drift(self):
        # coverage 出爐後磁碟又長出新 eligible 檔 → census/coverage/DB 三方失衡
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            _write(root / "knowledge" / "proj" / "late--sl-late.md",
                   "---\nslice_id: sl-late\nmemory_layer: knowledge\nproject: proj\n"
                   "title: 後補檔\n---\n建完索引後才出現的檔\n")
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("census files" in p for p in result.problems))
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))

    def test_reconcile_index_reports_unreadable_db(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            search.index_path(root).unlink()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("index unreadable" in p for p in result.problems))


class ClassifierDivergenceTests(unittest.TestCase):
    """防同源自證（spec §3.2）：census 分類規則必須是獨立雙寫實作。

    行為面：對 build_index 消費的生產 classifier 注入 bug（patch `search`
    模組綁定的名字），census 判定不受影響，reconcile 必須回報 divergence。
    結構面：靜態檢查 census 原始碼不得引用生產 classifier——若 census 改回
    共用生產邏輯，classifier 的 bug 會同時複製到兩邊、`eligible == indexed`
    照樣通過，行為面測試也會失去偵測力，故 import 面一併鎖死。
    """

    def test_census_source_is_decoupled_from_production_classifiers(self):
        src = Path(census.__file__).read_text(encoding="utf-8")
        for banned in ("from ..noise", "paulsha_hippo.noise", "pool_exclude_reason",
                       "classify_noise", "corpus_for_roots", "build_corpus"):
            self.assertNotIn(banned, src)

    def test_census_catches_pool_classifier_bug_injected_into_index_path(self):
        # 模擬生產 pool classifier 壞掉：review record 不再被排除 → 被索引。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            with mock.patch.object(search, "pool_exclude_reason", lambda fm: None):
                coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            # census 的雙寫規則仍把 sl-rev 判為 pool-excluded → 分佈與 ID 集雙重失衡
            self.assertTrue(any("pool-excluded" in p for p in result.problems))
            self.assertTrue(any("indexed but not eligible" in p for p in result.problems))
            self.assertEqual(result.eligible_ids, {"sl-good"})
            self.assertIn("sl-rev", result.indexed_ids)

    def test_census_catches_noise_classifier_bug_injected_into_index_path(self):
        # 模擬生產 noise classifier 壞掉：structural echo 不再被排除 → 被索引。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)

            def broken(frontmatter, body, *, doc_corpus=None):
                return NoiseVerdict(False, "")

            with mock.patch.object(search, "classify_noise", broken):
                coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("noise-excluded" in p for p in result.problems))
            self.assertTrue(any("indexed but not eligible" in p for p in result.problems))
            self.assertNotIn("sl-echo", result.eligible_ids)
            self.assertIn("sl-echo", result.indexed_ids)


class IndexVerifyCliTests(unittest.TestCase):
    def test_cli_index_verify_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["problems"], [])
            self.assertEqual(payload["eligible"], payload["indexed"])

    def test_cli_index_verify_without_coverage_report_errors(self):
        with TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", tmp])
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertIn("error", payload)

    def test_cli_index_verify_detects_drift(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            _write(root / "knowledge" / "proj" / "late--sl-late.md",
                   "---\nslice_id: sl-late\nmemory_layer: knowledge\nproject: proj\n"
                   "title: 後補檔\n---\n建完索引後才出現的檔\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertTrue(payload["problems"])


if __name__ == "__main__":
    unittest.main()
