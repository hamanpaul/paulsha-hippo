from __future__ import annotations

import io
import json
import os
import pathlib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.cli import main


def _slice(root: Path, project: str, name: str, body: str) -> Path:
    p = root / "knowledge" / project / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nslice_id: {name.split('--')[-1].replace('.md','')}\nmemory_layer: knowledge\n"
        f"project: {project}\nartifact_kind: report\n---\n{body}\n", encoding="utf-8")
    return p


class PruneNoiseTests(unittest.TestCase):
    def _seed(self, root: Path):
        noise = _slice(root, "p", "cwd--sl-n1.md", "## CWD\n/home/paul_chen")
        good = _slice(root, "p", "versioning-and-release-policy--sl-g1.md",
                      "本 repo 採 conventional commit；tag 必須等於 VERSION，里程碑改用 marker branch。")
        return noise, good

    def test_dry_run_does_not_delete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            noise, good = self._seed(root)
            rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                       "--now", "2026-06-25T00:00:00Z", "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertTrue(noise.exists())
            self.assertTrue(good.exists())

    def test_apply_deletes_noise_keeps_good_and_writes_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            noise, good = self._seed(root)
            rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                       "--now", "2026-06-25T00:00:00Z", "--apply"])
            self.assertEqual(rc, 0)
            self.assertFalse(noise.exists())
            self.assertTrue(good.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            rows = [json.loads(l) for l in manifests[0].read_text().splitlines() if l.strip()]
            self.assertTrue(any(r["reason"].startswith("structural-echo") for r in rows))
            self.assertTrue((root / "knowledge" / "p-moc.md").exists())

    def test_durable_manifest_exists_before_any_delete(self):
        # #139 finding 2: a durable manifest of intended deletions must be written
        # BEFORE any unlink, so a later write failure can't leave deletes unaudited.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            ledger = root / "runtime" / "ledger"
            real_unlink = pathlib.Path.unlink
            manifest_present_at_first_unlink = []

            def spy_unlink(self_path, *a, **k):
                manifest_present_at_first_unlink.append(
                    bool(list(ledger.glob("prune-*.jsonl"))) if ledger.exists() else False)
                return real_unlink(self_path, *a, **k)

            with mock.patch.object(pathlib.Path, "unlink", spy_unlink):
                rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                           "--now", "2026-06-25T00:00:00Z", "--apply"])
            self.assertEqual(rc, 0)
            self.assertTrue(manifest_present_at_first_unlink, "expected at least one unlink")
            self.assertTrue(manifest_present_at_first_unlink[0],
                            "manifest must exist before the first delete")

    def test_doc_fragment_pruned_only_when_instruction_root_given(self):
        # With an instruction doc supplied as corpus, a verbatim section slice is
        # pruned as doc-fragment; a real note not in the corpus survives. Without
        # any --instruction-root that points at real docs, the rule stays inert.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            docdir = Path(tmp) / "docs"
            docdir.mkdir()
            (docdir / "AGENTS.md").write_text(
                "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開\n",
                encoding="utf-8")
            frag = _slice(root, "p", "untitled--sl-f1.md",
                          "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開")
            good = _slice(root, "p", "real--sl-g2.md",
                          "本專案的 UART2 在 pinmux 設錯時會靜默失效，需用 devmem 確認暫存器。")
            rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                       "--now", "2026-06-25T00:00:00Z", "--instruction-root", str(docdir),
                       "--apply"])
            self.assertEqual(rc, 0)
            self.assertFalse(frag.exists())
            self.assertTrue(good.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            rows = [json.loads(l) for l in manifests[0].read_text().splitlines() if l.strip()]
            self.assertTrue(any(r["reason"] == "doc-fragment" for r in rows))

    def test_project_filter_limits_scope(self):
        # --project restricts pruning to the named projects; other projects untouched.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = _slice(root, "other", "cwd--sl-o1.md", "## CWD\n/home/paul_chen")
            drop = _slice(root, "p", "cwd--sl-p1.md", "## CWD\n/home/paul_chen")
            rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                       "--now", "2026-06-25T00:00:00Z", "--project", "p", "--apply"])
            self.assertEqual(rc, 0)
            self.assertFalse(drop.exists())
            self.assertTrue(keep.exists())   # 'other' project not in scope

    def test_apply_isolates_unreadable_file_without_deleting_it(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            noise, good = self._seed(root)
            bad = root / "knowledge" / "p" / "bad--sl-x.md"
            bad.write_bytes(b"\xff\xfe not utf8")
            rc = main(["knowledge", "prune-noise", "--memory-root", str(root),
                       "--now", "2026-06-25T00:00:00Z", "--apply"])
            self.assertEqual(rc, 0)
            self.assertTrue(good.exists())
            self.assertFalse(noise.exists())
            self.assertTrue(bad.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            rows = [json.loads(l) for l in manifests[0].read_text().splitlines() if l.strip()]
            bad_rows = [r for r in rows if r["path"] == str(bad)]
            self.assertEqual(len(bad_rows), 1)
            self.assertEqual(bad_rows[0]["status"], "error")
            self.assertEqual(bad_rows[0]["reason"], "unreadable")


class PruneListedTests(unittest.TestCase):
    def _seed(self, root: Path):
        listed = _slice(root, "serialwrap", "untitled--sl-u1.md", "## 語言政策\n所有溝通使用 zh-TW。")
        unlisted_noise = _slice(root, "serialwrap", "cwd--sl-n2.md", "## CWD\n/home/x")
        good = _slice(root, "serialwrap", "real--sl-g9.md", "真實筆記：devmem 驗證暫存器。")
        return listed, unlisted_noise, good

    def test_listed_apply_deletes_only_listed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, unlisted_noise, good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"# 固定清單\n{listed}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--apply",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertFalse(listed.exists())
            self.assertTrue(unlisted_noise.exists())
            self.assertTrue(good.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            rows = [json.loads(line) for line in manifests[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reason"], "listed")
            self.assertEqual(rows[0]["status"], "deleted")

    def test_listed_dry_run_deletes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{listed}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--dry-run",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertTrue(listed.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            rows = [json.loads(line) for line in manifests[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(rows[0]["status"], "dry-run")

    def test_listed_missing_path_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(
                f"{listed}\n{root}/knowledge/serialwrap/ghost--sl-x.md\n",
                encoding="utf-8",
            )

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--apply",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertTrue(listed.exists())

    def test_listed_outside_knowledge_root_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            outside = root / "outside.md"
            outside.write_text("---\nmemory_layer: knowledge\n---\nx\n", encoding="utf-8")
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{outside}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--apply",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertTrue(outside.exists())

    def test_listed_relative_path_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{os.path.relpath(listed, Path.cwd())}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--apply",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertTrue(listed.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl")) if (root / "runtime" / "ledger").exists() else []
            self.assertEqual(manifests, [])

    def test_listed_symlink_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            alias = root / "alias.md"
            alias.symlink_to(listed)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{alias}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--apply",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertTrue(alias.is_symlink())
            self.assertTrue(listed.exists())

    def test_listed_duplicate_path_fails_closed_before_delete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{listed}\n{listed}\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                rc = main(["knowledge",
                        "prune-noise",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-07-02T00:00:00Z",
                        "--paths",
                        str(paths_file),
                        "--apply",
                    ]
                )

            self.assertEqual(rc, 2)
            self.assertTrue(listed.exists())
            self.assertIn("duplicate", stderr.getvalue())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl")) if (root / "runtime" / "ledger").exists() else []
            self.assertEqual(manifests, [])

    def test_paths_mutually_exclusive_with_scan_filters(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{listed}\n", encoding="utf-8")

            rc = main(["knowledge",
                    "prune-noise",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--paths",
                    str(paths_file),
                    "--project",
                    "serialwrap",
                    "--dry-run",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertTrue(listed.exists())

    def test_listed_apply_unlink_error_returns_nonzero_and_reports_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, _noise, _good = self._seed(root)
            paths_file = root / "cleanup.txt"
            paths_file.write_text(f"{listed}\n", encoding="utf-8")
            stdout = io.StringIO()
            real_unlink = pathlib.Path.unlink

            def failing_unlink(self_path, *args, **kwargs):
                if self_path == listed:
                    raise OSError("disk busy")
                return real_unlink(self_path, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "unlink", failing_unlink):
                with redirect_stdout(stdout):
                    rc = main(["knowledge",
                            "prune-noise",
                            "--memory-root",
                            str(root),
                            "--now",
                            "2026-07-02T00:00:00Z",
                            "--paths",
                            str(paths_file),
                            "--apply",
                        ]
                    )

            self.assertEqual(rc, 1)
            self.assertTrue(listed.exists())
            manifests = list((root / "runtime" / "ledger").glob("prune-*.jsonl"))
            rows = [json.loads(line) for line in manifests[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(rows[0]["status"], "error")
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["deleted"], 0)
            self.assertEqual(summary["errors"], 1)


if __name__ == "__main__":
    unittest.main()
