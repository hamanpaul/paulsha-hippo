from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.lib.lifecycle.schema import compute_checksum
from paulsha_hippo.atomizer import slice_frontmatter
from paulsha_hippo.replay import bundle, cli


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _tmp_dir():
    return TemporaryDirectory(dir=_REPO_ROOT)


def _slice(
    root: Path,
    slice_id: str,
    *,
    filename: str | None = None,
    quote_slice_id: bool = False,
    quote_distilled_from: bool = False,
    distilled_from: str = "claude:s1",
) -> Path:
    name = filename or f"{slice_id}.md"
    path = root / "knowledge" / "p" / name
    path.parent.mkdir(parents=True, exist_ok=True)

    body = f"DISTILLED {slice_id}\n"
    frontmatter: dict[str, object] = {
        # Stage 3 required
        "phase": "review",
        "project": "p",
        "slice_id": slice_id,
        "artifact_kind": "report",
        "version": "1",
        "created_at": "2026-06-02T00:00:00Z",
        "created_by": "claude",
        "source_session": "s1",
        "gate_required": False,
        "checksum": compute_checksum(body),
        # T4 read contract
        "memory_layer": "knowledge",
        "source_agent": "claude",
        "captured_at": "2026-06-02T00:00:00Z",
        "provenance": {"repo": "r", "commit": "c", "path": "p"},
        "supersedes": [],
        # derivation
        "distilled_from": distilled_from,
    }

    text = slice_frontmatter.render(
        slice_frontmatter.Slice(slice_id=slice_id, frontmatter=frontmatter, body=body)
    )

    if quote_slice_id:
        text = re.sub(
            rf"(?m)^slice_id:\s*{re.escape(slice_id)}\s*$",
            f'slice_id: "{slice_id}"',
            text,
            count=1,
        )
    if quote_distilled_from:
        text = re.sub(
            rf"(?m)^distilled_from:\s*{re.escape(distilled_from)}\s*$",
            f'distilled_from: "{distilled_from}"',
            text,
            count=1,
        )

    path.write_text(text, encoding="utf-8")
    return path


def _raw_inbox(root: Path) -> Path:
    path = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "memory_layer: inbox\n"
        "project: p\n"
        "source_agent: claude\n"
        "source_session: s1\n"
        "source_artifact: research\n"
        "captured_at: 2026-06-02T00:00:00Z\n"
        "---\n"
        "RAW PROMPT CONTENT\n",
        encoding="utf-8",
    )
    return path


class ReplayBundleTests(unittest.TestCase):
    def test_build_rejects_raw_inbox_markdown_as_slice(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            raw = _raw_inbox(root)
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [raw], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertFalse(out.exists())

    def test_build_rejects_malformed_frontmatter_as_slice(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            bad = root / "knowledge" / "p" / "bad.md"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text(
                "---\n"
                "slice_id: [unterminated\n"
                "memory_layer: knowledge\n"
                "---\n"
                "NOT REALLY A SLICE\n",
                encoding="utf-8",
            )
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [bad], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertFalse(out.exists())

    def test_build_rejects_slice_id_with_path_separators_or_traversal(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "../../outside", filename="unsafe.md")
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertFalse(out.exists())

    def test_build_rejects_knowledge_markdown_missing_distilled_from(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = root / "knowledge" / "p" / "missing-distilled-from.md"
            s.parent.mkdir(parents=True, exist_ok=True)
            s.write_text(
                "---\n"
                "slice_id: sl-missing\n"
                "project: p\n"
                "memory_layer: knowledge\n"
                "---\n"
                "NOT REALLY DISTILLED\n",
                encoding="utf-8",
            )
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertFalse(out.exists())

    def test_build_rejects_forged_under_specified_knowledge_markdown_as_slice(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            forged = root / "knowledge" / "p" / "forged.md"
            forged.parent.mkdir(parents=True, exist_ok=True)
            forged.write_text(
                "---\n"
                "slice_id: sl-forged\n"
                "project: p\n"
                "memory_layer: knowledge\n"
                "distilled_from: claude:s1\n"
                "---\n"
                "FORGED\n",
                encoding="utf-8",
            )
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [forged], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertFalse(out.exists())

    def test_build_writes_manifest_slices_ledger_and_excludes_raw(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _raw_inbox(root)
            s = _slice(root, "sl-1")
            out = root / "bundle-out"

            bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["raw_excluded"])
            self.assertEqual(manifest["counts"]["slices"], 1)
            self.assertTrue((out / "slices" / "sl-1.md").exists())
            self.assertTrue((out / "ledger.jsonl").exists())

            blob = "".join(
                p.read_text(encoding="utf-8")
                for p in out.rglob("*")
                if p.is_file()
            )
            self.assertIn("DISTILLED", blob)
            self.assertNotIn("RAW PROMPT CONTENT", blob)

    def test_rebuild_does_not_destroy_previous_bundle_on_failure(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s1 = _slice(root, "sl-1")
            out = root / "bundle-out"

            bundle.build(root, [s1], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")
            original_manifest = (out / "manifest.json").read_text(encoding="utf-8")
            original_slice = (out / "slices" / "sl-1.md").read_text(encoding="utf-8")

            s2 = _slice(root, "sl-2")
            with mock.patch(
                "paulsha_hippo.replay.bundle.shutil.copyfile",
                side_effect=OSError("boom"),
            ):
                with self.assertRaises(bundle.BundleError):
                    bundle.build(
                        root,
                        [s2],
                        out,
                        selection={"project": "p"},
                        now="2026-06-02T06:01:00Z",
                    )

            self.assertEqual((out / "manifest.json").read_text(encoding="utf-8"), original_manifest)
            self.assertEqual((out / "slices" / "sl-1.md").read_text(encoding="utf-8"), original_slice)
            self.assertTrue((out / "slices" / "sl-1.md").exists())
            self.assertFalse((out / "slices" / "sl-2.md").exists())

    def test_rebuild_cleans_stale_slices(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s1 = _slice(root, "sl-1")
            out = root / "bundle-out"

            bundle.build(root, [s1], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")
            self.assertTrue((out / "slices" / "sl-1.md").exists())

            (out / "slices" / "stale.md").write_text("STALE\n", encoding="utf-8")
            s2 = _slice(root, "sl-2")
            bundle.build(root, [s2], out, selection={"project": "p"}, now="2026-06-02T06:01:00Z")

            self.assertFalse((out / "slices" / "stale.md").exists())
            self.assertFalse((out / "slices" / "sl-1.md").exists())
            self.assertTrue((out / "slices" / "sl-2.md").exists())

    def test_rebuild_cleans_stale_root_files_and_dirs(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s1 = _slice(root, "sl-1")
            out = root / "bundle-out"

            bundle.build(root, [s1], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")
            self.assertTrue((out / "manifest.json").exists())

            (out / "stale-root.txt").write_text("STALE\n", encoding="utf-8")
            (out / "stale-dir").mkdir(parents=True, exist_ok=True)
            (out / "stale-dir" / "nested.txt").write_text("STALE\n", encoding="utf-8")

            s2 = _slice(root, "sl-2")
            bundle.build(root, [s2], out, selection={"project": "p"}, now="2026-06-02T06:01:00Z")

            self.assertFalse((out / "stale-root.txt").exists())
            self.assertFalse((out / "stale-dir").exists())
            self.assertTrue((out / "slices" / "sl-2.md").exists())

    def test_out_dir_under_knowledge_is_rejected(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "sl-1")
            out = root / "knowledge" / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

    def test_empty_selection_writes_empty_bundle_with_warning(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            out = root / "bundle-out"

            bundle.build(root, [], out, selection={"project": "none"}, now="2026-06-02T06:00:00Z")

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["slices"], 0)
            self.assertIn("warnings", manifest)
            self.assertTrue(any("empty" in w for w in manifest["warnings"]))

    def test_corrupt_lifecycle_ledger_emits_warning(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "sl-1")
            out = root / "bundle-out"

            lifecycle_path = root / "runtime" / "ledger" / "lifecycle.jsonl"
            lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
            lifecycle_path.write_text("{not-json}\n", encoding="utf-8")

            bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("warnings", manifest)
            self.assertTrue(any("lifecycle" in w for w in manifest["warnings"]))
            self.assertTrue((out / "ledger.jsonl").exists())

    def test_duplicate_slice_ids_raise_bundle_error(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s1 = _slice(root, "sl-dup", filename="a.md")
            s2 = _slice(root, "sl-dup", filename="b.md")
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s1, s2], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

    def test_build_parses_quoted_frontmatter_values_for_slice_id_and_distilled_from(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "sl-q", quote_slice_id=True, quote_distilled_from=True)
            out = root / "bundle-out"

            lifecycle_path = root / "runtime" / "ledger" / "lifecycle.jsonl"
            lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
            lifecycle_path.write_text(
                json.dumps({"record_id": "sl-q"}) + "\n",
                encoding="utf-8",
            )

            processing_path = root / "runtime" / "ledger" / "processing.jsonl"
            processing_path.parent.mkdir(parents=True, exist_ok=True)
            processing_path.write_text(
                json.dumps({"session_key": "claude:s1"}) + "\n",
                encoding="utf-8",
            )

            bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

            self.assertTrue((out / "slices" / "sl-q.md").exists())
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["slice_ids"], ["sl-q"])

            ledger_lines = (out / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in ledger_lines if line.strip()]
            self.assertTrue(any(e.get("ledger") == "lifecycle" and e.get("record_id") == "sl-q" for e in events))
            self.assertTrue(any(e.get("ledger") == "processing" and e.get("session_key") == "claude:s1" for e in events))

    def test_cli_entity_selection_with_malformed_relations_ledger_returns_clean_error(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1")
            out = root / "bundle-out"

            relations_path = root / "runtime" / "ledger" / "relations.jsonl"
            relations_path.parent.mkdir(parents=True, exist_ok=True)
            relations_path.write_text("{not-json}\n", encoding="utf-8")

            args = argparse.Namespace(
                memory_root=str(root),
                project=None,
                tag=None,
                entity="e1",
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.run(args)

            self.assertNotEqual(code, 0)
            msg = stderr.getvalue()
            self.assertIn("selector error", msg)
            self.assertNotIn("Traceback", msg)

    def test_cli_warns_but_succeeds_on_empty_selection(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            out = root / "bundle-out"
            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                mock.patch("paulsha_hippo.replay.selector.select", return_value=[]),
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                code = cli.run(args)

            self.assertEqual(code, 0)
            self.assertIn("empty", stderr.getvalue().lower())
            self.assertIn(str(out), stdout.getvalue())

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(any("empty" in w for w in manifest.get("warnings", [])))

    def test_cli_degrades_on_corrupt_lifecycle_ledger_in_selection(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1")
            out = root / "bundle-out"

            lifecycle_path = root / "runtime" / "ledger" / "lifecycle.jsonl"
            lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
            lifecycle_path.write_text("{not-json}\n", encoding="utf-8")

            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                code = cli.run(args)

            self.assertEqual(code, 0)
            err = stderr.getvalue().lower()
            self.assertIn("active filtering", err)
            self.assertIn(str(out), stdout.getvalue())

    def test_cli_surfaces_manifest_warnings_to_stderr(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1")
            out = root / "bundle-out"

            lifecycle_path = root / "runtime" / "ledger" / "lifecycle.jsonl"
            lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
            lifecycle_path.write_text("{not-json}\n", encoding="utf-8")

            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=True,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                code = cli.run(args)

            self.assertEqual(code, 0)
            err = stderr.getvalue().lower()
            self.assertIn("lifecycle ledger unreadable", err)
            self.assertIn(str(out), stdout.getvalue())

    def test_cli_out_path_is_file_returns_clean_error(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            out = root / "bundle-out"
            out.write_text("NOT A DIR\n", encoding="utf-8")
            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            with (
                mock.patch("paulsha_hippo.replay.selector.select", return_value=[]),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.run(args)

            self.assertNotEqual(code, 0)
            msg = stderr.getvalue()
            self.assertIn("bundle error", msg)
            self.assertIn("not a directory", msg.lower())
            self.assertNotIn("Traceback", msg)

    def test_cli_surfaces_selector_error_cleanly(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            out = root / "bundle-out"
            args = argparse.Namespace(
                memory_root=str(root),
                project=None,
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.run(args)

            self.assertNotEqual(code, 0)
            self.assertIn("facet", stderr.getvalue())

    def test_cli_surfaces_relations_ledger_failure_cleanly(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "sl-1")
            out = root / "bundle-out"
            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            with (
                mock.patch("paulsha_hippo.replay.selector.select", return_value=[s]),
                mock.patch(
                    "paulsha_hippo.replay.bundle.relations.read_edges",
                    side_effect=Exception("boom"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.run(args)

            self.assertNotEqual(code, 0)
            self.assertIn("bundle error", stderr.getvalue())

    def test_cli_surfaces_processing_ledger_failure_cleanly(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            s = _slice(root, "sl-1")
            out = root / "bundle-out"
            args = argparse.Namespace(
                memory_root=str(root),
                project="p",
                tag=None,
                entity=None,
                include_decayed=False,
                out=str(out),
                now="2026-06-02T06:00:00Z",
            )

            stderr = io.StringIO()
            with (
                mock.patch("paulsha_hippo.replay.selector.select", return_value=[s]),
                mock.patch(
                    "paulsha_hippo.replay.bundle.processing.read_events",
                    side_effect=Exception("boom"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.run(args)

            self.assertNotEqual(code, 0)
            self.assertIn("bundle error", stderr.getvalue())

    def test_build_rejects_non_string_slice_id_types(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            # create a valid slice then mutate slice_id to a non-string YAML value
            s = _slice(root, "sl-valid")
            text = s.read_text(encoding="utf-8")
            text = re.sub(r"(?m)^slice_id:\s*sl-valid\s*$", "slice_id: [1,2,3]", text, count=1)
            s.write_text(text, encoding="utf-8")
            out = root / "bundle-out"

            # current behavior should raise BundleError once we enforce type checking
            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")

    def test_build_rejects_non_string_distilled_from_types(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            # create a valid slice then mutate distilled_from to a non-string YAML value
            s = _slice(root, "sl-valid-2", distilled_from="claude:s1")
            text = s.read_text(encoding="utf-8")
            text = re.sub(r"(?m)^distilled_from:\s*claude:s1\s*$", "distilled_from: 12345", text, count=1)
            s.write_text(text, encoding="utf-8")
            out = root / "bundle-out"

            with self.assertRaises(bundle.BundleError):
                bundle.build(root, [s], out, selection={"project": "p"}, now="2026-06-02T06:00:00Z")


if __name__ == "__main__":
    unittest.main()
