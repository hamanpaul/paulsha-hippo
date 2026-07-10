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
from paulsha_hippo.moc import census, naming, runner, search
from paulsha_hippo.moc import frontmatter_io as fio
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


class FtsCorruptionTests(unittest.TestCase):
    """FTS-only corruption 不得 false green（Codex review blocking）。

    實際搜尋走 slices_fts INNER JOIN slice_meta（search.search()）——
    FTS row 遺失／重複而 slice_meta 完整時，舊實作（只反查 slice_meta）
    的 `hippo index verify` 照樣回綠，但使用者搜尋漏資料或重複。以下
    每一測都是「metadata 面完好、只有 FTS 面壞」的注入。
    """

    def _built_root(self, tmp: str) -> "tuple[Path, dict]":
        root = Path(tmp)
        _seed_mixed_tree(root)
        coverage = search.build_index(root, link_weights={})
        return root, coverage

    def test_reconcile_detects_fts_missing_row(self):
        # FTS 掉行、slice_meta 完整：實際搜尋漏資料
        with TemporaryDirectory() as tmp:
            root, coverage = self._built_root(tmp)
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DELETE FROM slices_fts WHERE slice_id = 'sl-good'")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("missing from slices_fts" in p for p in result.problems))
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))
            self.assertNotIn("sl-good", result.indexed_ids)  # 不可搜尋 → 不算 indexed

    def test_reconcile_detects_fts_duplicate_row(self):
        # FTS 重複行（FTS5 無唯一約束）：實際搜尋重複回傳同一 slice
        with TemporaryDirectory() as tmp:
            root, coverage = self._built_root(tmp)
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("INSERT INTO slices_fts VALUES "
                         "('sl-good','proj','索引良品','t','真實 知識 內容')")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("duplicate slices_fts rows" in p for p in result.problems))

    def test_reconcile_detects_fts_phantom_row(self):
        # 幽靈 FTS 行（無對應 slice_meta）：inner join 靜默丟棄，兩表失衡必須顯性
        with TemporaryDirectory() as tmp:
            root, coverage = self._built_root(tmp)
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("INSERT INTO slices_fts VALUES "
                         "('sl-ghost','proj','幽靈','t','ghost body')")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("missing from slice_meta" in p for p in result.problems))
            self.assertNotIn("sl-ghost", result.indexed_ids)

    def test_reconcile_detects_fts_index_corruption(self):
        # 倒排索引 shadow table 掏空：FTS integrity-check 必須顯性回報
        with TemporaryDirectory() as tmp:
            root, coverage = self._built_root(tmp)
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DELETE FROM slices_fts_data")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("integrity-check failed" in p for p in result.problems))


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


class IdentityDivergenceTests(unittest.TestCase):
    """防同源自證（Codex review blocking）：fate/eligible 身份必須以 census
    自身 line-based 獨立解析（CensusEntry）為基準，並逐檔與 fio.read 交叉比對。

    舊實作 reconcile 迴圈只用 entry.path、再經與 build_index 共用的 fio.read
    重讀 ID——共用 parser 誤判磁碟 ID（合法 YAML tag/anchor、或 parser 本身
    的 bug）時，eligible 端與 DB 端拿到同一個錯 ID，reconcile 照樣 ok=true、
    problems=[]（false green）。以下每一測在舊實作下都回綠。
    """

    def test_yaml_tagged_slice_id_is_flagged_not_false_green(self):
        # 合法 YAML tag：磁碟原文 `!!str sl-tagged`，共用 parser 解成 `sl-tagged`
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            _write(root / "knowledge" / "proj" / "tagged--sl-tagged.md",
                   "---\nslice_id: !!str sl-tagged\nmemory_layer: knowledge\n"
                   "project: proj\ntitle: tagged-note\n---\nyaml tag 身份 內容\n")
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("identity divergence" in p and "tagged--sl-tagged.md" in p
                                and "slice_id" in p for p in result.problems),
                            result.problems)
            # eligible 基準是 census 的獨立磁碟 ID；DB 端是共用 parser 的產物
            self.assertIn("!!str sl-tagged", result.eligible_ids)
            self.assertIn("sl-tagged", result.indexed_ids)
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))
            self.assertTrue(any("indexed but not eligible" in p for p in result.problems))

    def test_yaml_anchored_slice_id_is_flagged_not_false_green(self):
        # 合法 YAML anchor：磁碟原文 `&sid sl-anchored`，共用 parser 解成 `sl-anchored`
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            _write(root / "knowledge" / "proj" / "anchored--sl-anchored.md",
                   "---\nslice_id: &sid sl-anchored\nmemory_layer: knowledge\n"
                   "project: proj\ntitle: anchored-note\n---\nyaml anchor 身份 內容\n")
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("identity divergence" in p and "anchored--sl-anchored.md" in p
                                for p in result.problems), result.problems)
            self.assertIn("&sid sl-anchored", result.eligible_ids)
            self.assertIn("sl-anchored", result.indexed_ids)

    def test_yaml_tagged_memory_layer_is_flagged_not_false_green(self):
        # memory_layer 身份同樣以 census 為基準：`!!str knowledge` 兩 parser 分歧
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            _write(root / "knowledge" / "proj" / "taglayer--sl-taglayer.md",
                   "---\nslice_id: sl-taglayer\nmemory_layer: !!str knowledge\n"
                   "project: proj\ntitle: taglayer-note\n---\nyaml tag layer 內容\n")
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("identity divergence" in p and "memory_layer" in p
                                for p in result.problems), result.problems)
            # census 判 pool:non-knowledge-layer、build 判 eligible → 分佈與 ID 集雙重失衡
            self.assertNotIn("sl-taglayer", result.eligible_ids)
            self.assertIn("sl-taglayer", result.indexed_ids)
            self.assertTrue(any("indexed but not eligible" in p for p in result.problems))

    def test_shared_parser_id_corruption_is_flagged_not_false_green(self):
        # 共用 parser 注入 ID bug：build_index 與 census 消費的 fio.read 同時
        # 吐錯 ID——舊實作兩邊拿到同一個錯值、全綠；census 獨立解析必須抓到。
        real_read = fio.read

        def poisoned(text):
            fm, body = real_read(text)
            if isinstance(fm, dict) and fm.get("slice_id"):
                fm = dict(fm)
                fm["slice_id"] = f"{fm['slice_id']}-corrupt"
            return fm, body

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            with mock.patch.object(fio, "read", poisoned):
                coverage = search.build_index(root, link_weights={})
                result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("identity divergence" in p and "slice_id" in p
                                for p in result.problems), result.problems)
            self.assertEqual(result.eligible_ids, {"sl-good"})  # 獨立解析不受污染
            self.assertIn("sl-good-corrupt", result.indexed_ids)
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))
            self.assertTrue(any("indexed but not eligible" in p for p in result.problems))

    def test_production_parser_dropping_census_visible_identity_is_flagged(self):
        # 生產 parser 判無 frontmatter（未閉合 ---）、census line-based 卻讀得到
        # 身份欄位：磁碟 ID 全集角度必須顯性回報，不得沉默歸 invalid 了事。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            _write(root / "knowledge" / "proj" / "lost--sl-lost.md",
                   "---\nslice_id: sl-lost\nmemory_layer: knowledge\nbody 未閉合\n")
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("identity divergence" in p and "lost--sl-lost.md" in p
                                and "found none" in p for p in result.problems),
                            result.problems)
            self.assertNotIn("sl-lost", result.eligible_ids)
            self.assertNotIn("sl-lost", result.indexed_ids)


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

    def test_cli_index_verify_reads_db_coverage_when_json_missing(self):
        # 派生 JSON 缺席（衍生失敗情境）：verify 以 DB 內權威 coverage 對賬
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            search.coverage_path(root).unlink()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_cli_index_verify_falls_back_to_json_for_legacy_db(self):
        # coverage 表出現前的舊版 DB：退回讀派生 JSON，不無故報缺
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DROP TABLE coverage")
            conn.commit()
            conn.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])

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


class IndexRebuildE2ETests(unittest.TestCase):
    def test_overlong_title_poison_noise_full_chain(self):
        """#16 全鏈驗收：超長 title 正常收編、壞 slice fail-soft 不中止整輪、
        excluded 各有去向、三方對賬全綠（動態計算，不寫死數字）。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            _write(k / "long.md",
                   "---\nslice_id: sl-long\nmemory_layer: knowledge\nproject: proj\n"
                   "artifact_kind: research\ntitle: " + "超長標題" * 80 + "\n"
                   "captured_at: 2026-07-10T00:00:00Z\n---\n超長標題 slice 的真實內容\n")
            k.joinpath("broken.md").write_bytes(b"---\nslice_id: sl-broken\n\xff\xfe---\nbody\n")
            _write(k / "rev--sl-rev.md",
                   "---\nslice_id: sl-rev\nmemory_layer: knowledge\nproject: proj\n"
                   "title: PR Review\nartifact_kind: review\n---\nreview body\n")
            _write(k / "echo--sl-echo.md",
                   "---\nslice_id: sl-echo\nmemory_layer: knowledge\nproject: proj\n"
                   "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n")

            result = runner.run_moc(root, now="2026-07-10T00:00:00Z")

            # 超長 title：rename 成功、byte-bound、無 ENAMETOOLONG
            renamed = [p.name for p in k.iterdir() if p.name.endswith("--sl-long.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].encode("utf-8")), 255)
            # 壞 slice fail-soft：整輪未中止、索引照建、warning 有記
            self.assertTrue(result["indexed"])
            self.assertTrue(any("broken.md" in w for w in result["warnings"]))
            # 強不變量（三方對賬版）：indexed IDs == eligible IDs
            verdict = census.reconcile_index(root, result["index_coverage"])
            self.assertEqual(verdict.problems, [])
            self.assertTrue(verdict.ok)
            self.assertIn("sl-long", verdict.indexed_ids)
            self.assertNotIn("sl-rev", verdict.indexed_ids)     # pool-excluded 有去向
            self.assertNotIn("sl-broken", verdict.indexed_ids)  # invalid 有去向
            # CLI 全鏈（讀 coverage 落盤 + DB 反查）
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_wrong_typed_tags_poison_slice_fail_soft_full_chain(self):
        """#16 同類失效鏈（Codex review blocking）：合法 YAML 但 tags 錯型
        （``tags: [1]``）曾讓 row 組裝的 `" ".join` 丟 TypeError 炸掉整批
        build_index——健康 slice 不被發布、已有舊 DB 時持續提供 stale index。
        修正後：毒 slice 歸 invalid_frontmatter 並記路徑 warning，其餘照常
        索引，三方對賬全綠（census 雙寫同一 tags 型別規則、分佈對齊）。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            _write(k / "poison--sl-poison.md",
                   "---\nslice_id: sl-poison\nmemory_layer: knowledge\nproject: proj\n"
                   "title: poison-tags\ntags: [1]\ncaptured_at: 2026-07-10T00:00:00Z\n"
                   "---\n毒 tags 內容\n")
            _write(k / "healthy--sl-ok.md",
                   "---\nslice_id: sl-ok\nmemory_layer: knowledge\nproject: proj\n"
                   "title: healthy\ntags: [t]\ncaptured_at: 2026-07-10T00:00:00Z\n"
                   "---\n健康 無關 內容\n")

            result = runner.run_moc(root, now="2026-07-10T00:00:00Z")  # 不得 raise

            # 整批未中止：索引照建、健康 slice 發布可搜
            self.assertTrue(result["indexed"])
            cov = result["index_coverage"]
            self.assertEqual(cov["invalid_frontmatter"], 1)
            self.assertEqual(cov["eligible"], 1)
            self.assertEqual(cov["indexed"], 1)
            self.assertTrue(any("invalid tags" in w and "sl-poison" in w
                                for w in result["warnings"]), result["warnings"])
            hits = search.search(root, "健康", project=None, limit=5,
                                 include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-ok"])
            # 三方對賬全綠：census 雙寫的 tags 型別規則與 build 分佈對齊
            verdict = census.reconcile_index(root, cov)
            self.assertEqual(verdict.problems, [])
            self.assertTrue(verdict.ok)
            self.assertEqual(verdict.eligible_ids, {"sl-ok"})
            self.assertEqual(verdict.indexed_ids, {"sl-ok"})
            # CLI 全鏈
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_duplicate_slice_id_residue_fail_soft_full_chain(self):
        """#16 fail-soft 缺口（review blocking）：naming dedup 中途失敗（如
        ENOSPC）fail-soft 跳過後，磁碟殘留兩個同 slice_id 檔——build_index
        曾被 slice_meta PK 的 IntegrityError 整批炸掉（run_moc 回報
        indexed=False），連健康無關檔都不被索引。修正後：整輪照常完成、
        健康檔可搜、重複對先到先贏，reconcile 分佈對齊且顯性回報磁碟異常。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            _write(k / "dup-alpha--sl-dup.md",
                   "---\nslice_id: sl-dup\nmemory_layer: knowledge\nproject: proj\n"
                   "title: dup-alpha\ncaptured_at: 2026-07-10T00:00:00Z\n---\n重複 內容 一\n")
            _write(k / "dup-beta--sl-dup.md",
                   "---\nslice_id: sl-dup\nmemory_layer: knowledge\nproject: proj\n"
                   "title: dup-beta\ncaptured_at: 2026-07-10T00:00:00Z\n---\n重複 內容 二\n")
            _write(k / "healthy--sl-ok.md",
                   "---\nslice_id: sl-ok\nmemory_layer: knowledge\nproject: proj\n"
                   "title: healthy\ncaptured_at: 2026-07-10T00:00:00Z\n---\n健康 無關 內容\n")

            # dedup 步驟注入一次性磁碟故障：naming.reconcile fail-soft（記
            # warning 跳過該檔），同 slice_id 的兩檔原樣留在磁碟上進入重建。
            with mock.patch.object(
                naming, "_append_superseded_event",
                side_effect=OSError(28, "No space left on device"),
            ):
                result = runner.run_moc(root, now="2026-07-10T00:00:00Z")

            # 整輪未中止：索引照建（不再 indexed=False / index_coverage={}）
            self.assertTrue(result["indexed"])
            self.assertTrue(any("reconcile skipped" in w for w in result["warnings"]))
            self.assertTrue(any("duplicate slice_id on disk sl-dup" in w
                                for w in result["warnings"]), result["warnings"])
            cov = result["index_coverage"]
            self.assertEqual(cov["eligible"], 2)
            self.assertEqual(cov["indexed"], 2)
            self.assertEqual(cov["pool_excluded"]["duplicate-slice-id-on-disk"], 1)
            # 健康無關檔本輪照常入索引可搜
            hits = search.search(root, "健康", project=None, limit=5,
                                 include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-ok"])
            # 三方對賬：分佈與 ID 集全對齊，唯一 problem 是磁碟重複異常的顯性回報
            verdict = census.reconcile_index(root, cov)
            self.assertEqual(verdict.problems, ["duplicate slice_id on disk: sl-dup"])
            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.eligible_ids, {"sl-dup", "sl-ok"})
            self.assertEqual(verdict.indexed_ids, {"sl-dup", "sl-ok"})


if __name__ == "__main__":
    unittest.main()
