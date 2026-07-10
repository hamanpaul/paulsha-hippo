from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import retitle
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.noise import build_corpus


def _slice(root: Path, project: str, name: str, body: str, *, title: str = "untitled") -> Path:
    p = root / "knowledge" / project / name
    p.parent.mkdir(parents=True, exist_ok=True)
    sid = name.split("--")[-1].replace(".md", "")
    p.write_text(
        f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: {project}\n"
        f"artifact_kind: report\nsession_title: \"# AGENTS.md instruct\"\ntitle: {title}\n"
        f"aliases:\n  - {title}\n---\n{body}\n",
        encoding="utf-8")
    return p


class RetitleUntitledTests(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _slice(root, "paulshaclaw", "untitled--sl-a1.md",
                       "## 測試與除錯重點\nUART2 在 pinmux 設錯時靜默失效，用 devmem 確認暫存器。")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=False,
                distill=lambda body: "UART2 除錯重點")
            self.assertTrue(p.exists())
            self.assertEqual(summary["retitled"], 0)
            self.assertEqual(summary["planned"], 1)

    def test_apply_retitles_and_renames_preserving_slice_id_and_body(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "## 測試與除錯重點\nUART2 在 pinmux 設錯時靜默失效，用 devmem 確認暫存器。"
            p = _slice(root, "paulshaclaw", "untitled--sl-a1.md", body)
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "UART2 除錯重點")
            self.assertFalse(p.exists())
            target = root / "knowledge" / "paulshaclaw" / "uart2-除錯重點--sl-a1.md"
            self.assertTrue(target.exists(), list((root / "knowledge" / "paulshaclaw").iterdir()))
            fm, new_body = fio.read(target.read_text(encoding="utf-8"))
            self.assertEqual(fm["slice_id"], "sl-a1")          # preserved
            self.assertEqual(fm["title"], "UART2 除錯重點")
            self.assertEqual(fm["atom_title"], "UART2 除錯重點")
            self.assertEqual(new_body.strip(), body)            # body untouched
            self.assertEqual(summary["retitled"], 1)
            manifests = list((root / "runtime" / "ledger").glob("retitle-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            rows = [json.loads(l) for l in manifests[0].read_text().splitlines() if l.strip()]
            self.assertEqual(rows[0]["slice_id"], "sl-a1")
            self.assertEqual(rows[0]["status"], "retitled")

    def test_doc_fragment_candidate_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = build_corpus([
                "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開\n"])
            frag = _slice(root, "p", "untitled--sl-d1.md",
                          "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "不該被叫到", doc_corpus=corpus)
            self.assertTrue(frag.exists())       # not renamed
            self.assertEqual(summary["retitled"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_distill_failure_skips_without_failing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _slice(root, "p", "untitled--sl-o1.md",
                       "## 問題\n某真實技術問題的描述與其根因分析。")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: None)          # gemma4 offline
            self.assertTrue(p.exists())
            self.assertEqual(summary["retitled"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_project_filter_limits_scope(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = _slice(root, "p", "untitled--sl-p9.md", "## 問題\n真實技術問題描述與根因。")
            out = _slice(root, "other", "untitled--sl-x9.md", "## 問題\n另一個真實技術問題。")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "某標題", projects=["p"])
            self.assertFalse(inp.exists())          # p in scope -> renamed
            self.assertTrue(out.exists())           # other out of scope -> untouched
            self.assertEqual(summary["retitled"], 1)

    def test_apply_bounds_overlong_distilled_title_filename(self):
        # 蒸餾出超長 title 時，rename 後檔名必須 byte-bound（走 naming.slice_filename）
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "p", "untitled--sl-long1.md",
                   "## 問題\n某真實技術問題的描述與其根因分析。")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "長" * 300)
            self.assertEqual(summary["retitled"], 1)
            renamed = [p for p in (root / "knowledge" / "p").iterdir()
                       if p.name.endswith("--sl-long1.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].name.encode("utf-8")), 255)

    def test_non_untitled_slice_is_ignored(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _slice(root, "p", "ci-gating--sl-k1.md",
                       "gh pr checks 沒有 --json。", title="ci-gating")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "不該被叫到")
            self.assertTrue(p.exists())
            self.assertEqual(summary["retitled"], 0)
            self.assertEqual(summary["planned"], 0)


class RetitleGenericTitleTests(unittest.TestCase):
    def test_generic_title_slice_is_retitled(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "COM0 斷線根因是 udev 權限規則沒套用，重插後 ttyUSB 編號漂移。"
            p = _slice(root, "testpilot", "report-testpilot--sl-r1.md", body, title="report-testpilot")
            summary = retitle.retitle_untitled(
                root,
                now="2026-07-02T00:00:00Z",
                apply=True,
                distill=lambda b: "COM0 斷線根因",
            )
            self.assertFalse(p.exists())
            target = root / "knowledge" / "testpilot" / "com0-斷線根因--sl-r1.md"
            self.assertTrue(target.exists(), list((root / "knowledge" / "testpilot").iterdir()))
            self.assertEqual(summary["retitled"], 1)

    def test_specific_title_slice_not_scanned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _slice(
                root,
                "testpilot",
                "uart2-tips--sl-s1.md",
                "UART2 在 pinmux 設錯時靜默失效。",
                title="UART2 除錯重點",
            )
            summary = retitle.retitle_untitled(
                root,
                now="2026-07-02T00:00:00Z",
                apply=True,
                distill=lambda b: "不該被呼叫",
            )
            self.assertTrue(p.exists())
            self.assertEqual(summary["candidates"], 0)


if __name__ == "__main__":
    unittest.main()
