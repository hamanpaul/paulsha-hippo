from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import rekey
from paulsha_hippo.moc import frontmatter_io as fio

OLD_KEY = "github.com/hamanpaul/testpilot"
OLD_DIR = "github.com__hamanpaul__testpilot"


def _slice(
    root: Path,
    dirname: str,
    project: str,
    name: str,
    body: str,
    *,
    title: str = "uart-fix",
) -> Path:
    path = root / "knowledge" / dirname / name
    path.parent.mkdir(parents=True, exist_ok=True)
    slice_id = name.split("--")[-1].removesuffix(".md")
    path.write_text(
        f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\n"
        f"project: {project}\nartifact_kind: report\ntitle: {title}\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


class RekeyProjectTests(unittest.TestCase):
    def test_dry_run_writes_manifest_and_touches_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "真實筆記內容一。")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=False,
            )

            self.assertTrue(path.exists())
            fm, _body = fio.read(path.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], OLD_KEY)
            self.assertEqual(summary["planned"], 1)
            self.assertEqual(summary["rekeyed"], 0)
            manifests = list((root / "runtime" / "ledger").glob("rekey-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            rows = [json.loads(line) for line in manifests[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(rows[0]["status"], "dry-run")
            self.assertEqual(rows[0]["from"], OLD_KEY)
            self.assertEqual(rows[0]["to"], "testpilot")

    def test_apply_moves_file_updates_frontmatter_and_rebuilds_moc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "真實筆記內容：UART2 pinmux 設錯時靜默失效。"
            path = _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", body)

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertFalse(path.exists())
            target = root / "knowledge" / "testpilot" / "uart-fix--sl-a1.md"
            self.assertTrue(target.exists())
            fm, new_body = fio.read(target.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], "testpilot")
            self.assertEqual(fm["slice_id"], "sl-a1")
            self.assertEqual(new_body.strip(), body)
            self.assertEqual(summary["rekeyed"], 1)
            self.assertTrue((root / "knowledge" / "testpilot-moc.md").exists())

    def test_apply_removes_emptied_source_dir_and_orphan_moc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")
            orphan = root / "knowledge" / f"{OLD_DIR}-moc.md"
            orphan.write_text("---\nmemory_layer: moc\n---\nstale\n", encoding="utf-8")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertFalse((root / "knowledge" / OLD_DIR).exists())
            self.assertFalse(orphan.exists())
            self.assertTrue(summary["removed_source_dir"])
            self.assertTrue(summary["removed_orphan_moc"])

    def test_conflict_target_is_skipped_and_source_not_stamped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "舊 key 內容")
            _slice(root, "testpilot", "testpilot", "uart-fix--sl-a1.md", "同名既有檔")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertTrue(src.exists())
            fm, _body = fio.read(src.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], OLD_KEY)
            self.assertEqual(summary["conflicts"], 1)
            self.assertEqual(summary["rekeyed"], 0)

    def test_conflict_source_stays_untouched_when_other_rows_rekey(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "ok-file--sl-a1.md", "可遷移內容", title="ok-file")
            conflict = _slice(
                root,
                OLD_DIR,
                OLD_KEY,
                "old-name--sl-c1.md",
                "衝突來源內容",
                title="conflict title",
            )
            _slice(
                root,
                "testpilot",
                "testpilot",
                "old-name--sl-c1.md",
                "同名既有檔",
                title="old-name",
            )
            original_text = conflict.read_text(encoding="utf-8")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertEqual(summary["rekeyed"], 1)
            self.assertEqual(summary["conflicts"], 1)
            self.assertTrue(summary["indexed"])
            self.assertTrue(conflict.exists())
            self.assertFalse((conflict.parent / "conflict-title--sl-c1.md").exists())
            current_text = conflict.read_text(encoding="utf-8")
            self.assertEqual(current_text, original_text)
            fm, _body = fio.read(current_text)
            self.assertEqual(fm["project"], OLD_KEY)
            self.assertFalse(
                any("UNIQUE constraint failed" in warning for warning in summary["warnings"])
            )

    def test_same_slice_id_under_target_slug_is_treated_as_conflict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _slice(
                root,
                OLD_DIR,
                OLD_KEY,
                "old-name--sl-c1.md",
                "舊 key 內容",
                title="old name",
            )
            _slice(
                root,
                "testpilot",
                "testpilot",
                "new-name--sl-c1.md",
                "同 slice 既有檔",
                title="new name",
            )
            original_text = source.read_text(encoding="utf-8")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertEqual(summary["conflicts"], 1)
            self.assertEqual(summary["rekeyed"], 0)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), original_text)

    def test_same_target_path_planned_twice_marks_later_row_conflict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "knowledge" / OLD_DIR / "foo.md"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_text(
                "---\nslice_id: sl-a1\nmemory_layer: knowledge\n"
                f"project: {OLD_KEY}\nartifact_kind: report\ntitle: foo\n---\n第一筆\n",
                encoding="utf-8",
            )
            second = root / "knowledge" / OLD_DIR / "sub" / "foo.md"
            second.parent.mkdir(parents=True, exist_ok=True)
            second.write_text(
                "---\nslice_id: sl-b1\nmemory_layer: knowledge\n"
                f"project: {OLD_KEY}\nartifact_kind: report\ntitle: foo\n---\n第二筆\n",
                encoding="utf-8",
            )
            original_second = second.read_text(encoding="utf-8")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertEqual(summary["rekeyed"], 1)
            self.assertEqual(summary["conflicts"], 1)
            self.assertFalse(first.exists())
            self.assertTrue((root / "knowledge" / "testpilot" / "foo--sl-a1.md").exists())
            self.assertTrue(second.exists())
            self.assertEqual(second.read_text(encoding="utf-8"), original_second)

    def test_apply_cleanup_failure_is_reported_in_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")
            source_dir = root / "knowledge" / OLD_DIR
            real_rmdir = Path.rmdir

            def failing_rmdir(self_path: Path):
                if self_path == source_dir:
                    raise OSError("directory busy")
                return real_rmdir(self_path)

            with mock.patch.object(Path, "rmdir", failing_rmdir), mock.patch(
                "paulsha_hippo.rekey.run_moc",
                return_value={"indexed": True, "warnings": ["index rebuilt"]},
            ) as run_moc_mock:
                summary = rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="testpilot",
                    now="2026-07-02T00:00:00Z",
                    apply=True,
                )

            self.assertEqual(summary["rekeyed"], 1)
            self.assertEqual(summary["errors"], 1)
            self.assertTrue(summary["indexed"])
            self.assertTrue(
                any("directory busy" in warning for warning in summary["warnings"])
            )
            self.assertIn("index rebuilt", summary["warnings"])
            run_moc_mock.assert_called_once()
            self.assertTrue((root / "knowledge" / "testpilot" / "uart-fix--sl-a1.md").exists())

    def test_conflict_stash_failure_restores_already_parked_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "ok-file--sl-a1.md", "可遷移內容", title="ok-file")
            first = _slice(
                root,
                OLD_DIR,
                OLD_KEY,
                "first-conflict--sl-c1.md",
                "第一個 conflict",
                title="first conflict",
            )
            second = _slice(
                root,
                OLD_DIR,
                OLD_KEY,
                "second-conflict--sl-c2.md",
                "第二個 conflict",
                title="second conflict",
            )
            _slice(root, "testpilot", "testpilot", "first-conflict--sl-c1.md", "既有檔一", title="first-conflict")
            _slice(root, "testpilot", "testpilot", "second-conflict--sl-c2.md", "既有檔二", title="second-conflict")
            original_rename = Path.rename
            stash_moves: list[Path] = []

            def flaky_rename(self_path: Path, target: Path):
                if "rekey-conflicts-" in str(target.parent):
                    stash_moves.append(self_path)
                    if len(stash_moves) == 2:
                        raise OSError("stash blocked")
                return original_rename(self_path, target)

            with mock.patch.object(Path, "rename", flaky_rename):
                summary = rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="testpilot",
                    now="2026-07-02T00:00:00Z",
                    apply=True,
                )

            self.assertEqual(summary["errors"], 1)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_conflict_restore_failure_leaves_recovery_stash(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "ok-file--sl-a1.md", "可遷移內容", title="ok-file")
            _slice(root, OLD_DIR, OLD_KEY, "first-conflict--sl-c1.md", "第一個 conflict", title="first conflict")
            _slice(root, OLD_DIR, OLD_KEY, "second-conflict--sl-c2.md", "第二個 conflict", title="second conflict")
            _slice(root, "testpilot", "testpilot", "first-conflict--sl-c1.md", "既有檔一", title="first-conflict")
            _slice(root, "testpilot", "testpilot", "second-conflict--sl-c2.md", "既有檔二", title="second-conflict")
            original_rename = Path.rename

            def flaky_rename(self_path: Path, target: Path):
                if "rekey-conflicts-" in str(self_path.parent):
                    raise OSError("restore blocked")
                return original_rename(self_path, target)

            with mock.patch.object(Path, "rename", flaky_rename):
                summary = rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="testpilot",
                    now="2026-07-02T00:00:00Z",
                    apply=True,
                )

            self.assertEqual(summary["errors"], 1)
            self.assertTrue(any("restore blocked" in warning for warning in summary["warnings"]))
            stashes = sorted((root / "runtime").glob("rekey-conflicts-*"))
            self.assertTrue(stashes)
            self.assertTrue(any(any(stash.rglob("*.md")) for stash in stashes))

    def test_partial_rekey_keeps_old_project_moc_when_conflicts_remain(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "ok-file--sl-a1.md", "可遷移內容", title="ok-file")
            _slice(root, OLD_DIR, OLD_KEY, "old-name--sl-c1.md", "衝突來源內容", title="conflict title")
            _slice(root, "testpilot", "testpilot", "old-name--sl-c1.md", "同名既有檔", title="old-name")
            old_moc = root / "knowledge" / f"{OLD_DIR}-moc.md"
            old_moc.write_text("---\nmemory_layer: moc\n---\nstale\n", encoding="utf-8")

            summary = rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertEqual(summary["conflicts"], 1)
            self.assertFalse(summary["removed_orphan_moc"])
            self.assertTrue(old_moc.exists())

    def test_other_projects_untouched(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = _slice(
                root,
                "vendor-b",
                "vendor-b",
                "note--sl-b1.md",
                "vendor-b 真筆記",
                title="note",
            )
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")

            rekey.rekey_project(
                root,
                old_key=OLD_KEY,
                new_slug="testpilot",
                now="2026-07-02T00:00:00Z",
                apply=True,
            )

            self.assertTrue(other.exists())
            fm, _body = fio.read(other.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], "vendor-b")

    def test_unsafe_new_slug_raises(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(rekey.RekeyError):
                rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="a/b",
                    now="2026-07-02T00:00:00Z",
                    apply=False,
                )

    def test_apply_rename_failure_preserves_source_and_has_manifest_first(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")
            ledger = root / "runtime" / "ledger"
            manifest_present_at_rename: list[bool] = []

            def failing_rename(path_obj: Path, target: Path):
                manifest_present_at_rename.append(bool(list(ledger.glob("rekey-*.jsonl"))) if ledger.exists() else False)
                raise OSError("rename blocked")

            with mock.patch.object(Path, "rename", failing_rename):
                summary = rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="testpilot",
                    now="2026-07-02T00:00:00Z",
                    apply=True,
                )

            self.assertTrue(manifest_present_at_rename and manifest_present_at_rename[0])
            self.assertTrue(source.exists())
            fm, _body = fio.read(source.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], OLD_KEY)
            self.assertEqual(summary["errors"], 1)
            manifests = list(ledger.glob("rekey-*.jsonl"))
            rows = [json.loads(line) for line in manifests[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(rows[0]["status"], "error")

    def test_apply_surfaces_moc_rebuild_result(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")

            with mock.patch(
                "paulsha_hippo.rekey.run_moc",
                return_value={"indexed": False, "warnings": ["search index skipped: boom"]},
            ):
                summary = rekey.rekey_project(
                    root,
                    old_key=OLD_KEY,
                    new_slug="testpilot",
                    now="2026-07-02T00:00:00Z",
                    apply=True,
                )

            self.assertFalse(summary["indexed"])
            self.assertEqual(summary["warnings"], ["search index skipped: boom"])


class RekeyCliTests(unittest.TestCase):
    def test_cli_apply_moves_file(self):
        from paulsha_hippo.cli import main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")

            rc = main(["knowledge",
                    "rekey",
                    "--memory-root",
                    str(root),
                    "--from",
                    OLD_KEY,
                    "--to",
                    "testpilot",
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--apply",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertTrue((root / "knowledge" / "testpilot" / "uart-fix--sl-a1.md").exists())

    def test_cli_rejects_slash_in_to(self):
        from paulsha_hippo.cli import main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            rc = main(["knowledge",
                    "rekey",
                    "--memory-root",
                    str(root),
                    "--from",
                    OLD_KEY,
                    "--to",
                    "a/b",
                    "--now",
                    "2026-07-02T00:00:00Z",
                    "--dry-run",
                ]
            )

            self.assertEqual(rc, 2)
            ledger = root / "runtime" / "ledger"
            manifests = list(ledger.glob("rekey-*.jsonl")) if ledger.exists() else []
            self.assertEqual(manifests, [])

    def test_cli_surfaces_moc_rebuild_warnings(self):
        from paulsha_hippo.cli import main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")
            stderr = io.StringIO()

            with mock.patch(
                "paulsha_hippo.rekey.run_moc",
                return_value={"indexed": False, "warnings": ["search index skipped: boom"]},
            ):
                with redirect_stderr(stderr):
                    rc = main(["knowledge",
                            "rekey",
                            "--memory-root",
                            str(root),
                            "--from",
                            OLD_KEY,
                            "--to",
                            "testpilot",
                            "--now",
                            "2026-07-02T00:00:00Z",
                            "--apply",
                        ]
                    )

            self.assertEqual(rc, 1)
            self.assertIn("warning: search index skipped: boom", stderr.getvalue())

    def test_cli_rolls_back_after_frontmatter_update_failure(self):
        from paulsha_hippo.cli import main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _slice(root, OLD_DIR, OLD_KEY, "uart-fix--sl-a1.md", "內容")

            with mock.patch(
                "paulsha_hippo.rekey._fio.update",
                side_effect=OSError("frontmatter blocked"),
            ):
                rc = main(["knowledge",
                        "rekey",
                        "--memory-root",
                        str(root),
                        "--from",
                        OLD_KEY,
                        "--to",
                        "testpilot",
                        "--now",
                        "2026-07-02T00:00:00Z",
                        "--apply",
                    ]
                )

            self.assertEqual(rc, 1)
            self.assertTrue(source.exists())
            self.assertFalse((root / "knowledge" / "testpilot" / "uart-fix--sl-a1.md").exists())
            fm, _body = fio.read(source.read_text(encoding="utf-8"))
            self.assertEqual(fm["project"], OLD_KEY)
            manifests = list((root / "runtime" / "ledger").glob("rekey-*.jsonl"))
            rows = [
                json.loads(line)
                for line in manifests[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[0]["status"], "error")
            self.assertIn("rollback_error", rows[0])
            self.assertIsNone(rows[0]["rollback_error"])
            self.assertEqual(rows[0]["error"], "frontmatter blocked")


if __name__ == "__main__":
    unittest.main()
