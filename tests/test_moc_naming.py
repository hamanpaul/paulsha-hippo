from __future__ import annotations

import time
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger.lifecycle import read_events
from paulsha_hippo.ledger import retrieval_set
from paulsha_hippo.moc import naming


def _write(
    root: Path,
    name: str,
    slice_id: str,
    body: str = "body\n",
    *,
    title: str | None = None,
) -> Path:
    path = root / "knowledge" / "paulshaclaw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    title_line = f"title: {title}\n" if title else ""
    path.write_text(
        f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: paulshaclaw\n"
        f"artifact_kind: research\n{title_line}---\n{body}",
        encoding="utf-8",
    )
    return path


class NamingTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(naming.slugify("PWHM FSM States!"), "pwhm-fsm-states")

    def test_slugify_preserves_cjk(self):
        # #151: pure-CJK titles must not collapse to "untitled"; CJK is kept verbatim.
        self.assertEqual(naming.slugify("動工前"), "動工前")
        self.assertEqual(naming.slugify("修正 start.sh 啟動時 PYTHONPATH"), "修正-start-sh-啟動時-pythonpath")

    def test_slugify_ascii_unchanged(self):
        # Existing ASCII slugs are unaffected (zero churn on existing slices).
        self.assertEqual(naming.slugify("CI gating note"), "ci-gating-note")
        self.assertEqual(naming.slugify("6. 自主維護規則（agent-managed）"), "6-自主維護規則-agent-managed")

    def test_slugify_punctuation_only_falls_back(self):
        self.assertEqual(naming.slugify("---"), "untitled")
        self.assertEqual(naming.slugify(""), "untitled")

    def test_slugify_bounds_utf8_bytes_default(self):
        # #16 根因：超長 LLM title 未截斷，組出超過 NAME_MAX 的檔名
        slug = naming.slugify("測" * 100)  # 300 bytes
        self.assertLessEqual(len(slug.encode("utf-8")), naming.SLUG_MAX_BYTES_DEFAULT)
        self.assertTrue(slug)

    def test_slugify_truncates_at_codepoint_boundary(self):
        # "測" = 3 bytes：4/5 bytes 預算都只容得下一個完整字元，不得留半個
        self.assertEqual(naming.slugify("測測測", max_bytes=4), "測")
        self.assertEqual(naming.slugify("測測測", max_bytes=5), "測")
        self.assertEqual(naming.slugify("測測測", max_bytes=6), "測測")

    def test_slice_filename_never_exceeds_name_max(self):
        name = naming.slice_filename("記" * 120, "sl-0123456789abcdef")
        self.assertLessEqual(len(name.encode("utf-8")), naming.NAME_MAX_BYTES)
        self.assertTrue(name.endswith("--sl-0123456789abcdef.md"))

    def test_target_name_bounded_with_overlong_title(self):
        fm = {"slice_id": "sl-0123456789abcdef", "title": "超長標題" * 80}
        name = naming.target_name(fm, "body\n")
        self.assertLessEqual(len(name.encode("utf-8")), naming.NAME_MAX_BYTES)
        self.assertTrue(name.endswith("--sl-0123456789abcdef.md"))

    def test_reconcile_renames_overlong_title_without_enametoolong(self):
        # #16 根因情境：超長 title 也要 rename 成功、無 ENAMETOOLONG
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "sl-long.md", "sl-long", title="超長標題" * 80)
            warnings = naming.reconcile(root)
            self.assertEqual(warnings, [])
            renamed = [p for p in (root / "knowledge" / "paulshaclaw").iterdir()
                       if p.name.endswith("--sl-long.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].name.encode("utf-8")), naming.NAME_MAX_BYTES)

    def test_reconcile_renames_to_title_slice(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "knowledge" / "paulshaclaw" / "sl-1.md"
            p.parent.mkdir(parents=True)
            p.write_text("---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: paulshaclaw\n"
                         "artifact_kind: research\ntitle: Alpha Note\n---\nbody\n", encoding="utf-8")
            naming.reconcile(root)
            self.assertFalse(p.exists())
            self.assertTrue((root / "knowledge" / "paulshaclaw" / "alpha-note--sl-1.md").exists())

    def test_title_fallback_to_artifact_project(self):
        # no title, no heading -> <artifact_kind>-<project>
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "sl-2.md", "sl-2")
            naming.reconcile(root)
            self.assertTrue((root / "knowledge" / "paulshaclaw" / "research-paulshaclaw--sl-2.md").exists())

    def test_dedup_keeps_one_per_slice_id_and_records_lifecycle_event(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            older = _write(root, "first--sl-3.md", "sl-3", "OLD\n", title="first")
            time.sleep(0.01)
            newer = _write(root, "second--sl-3.md", "sl-3", "NEW\n", title="second")
            warnings = naming.reconcile(root)
            remaining = sorted(p.name for p in kdir.glob("*sl-3*.md"))
            self.assertEqual(remaining, ["second--sl-3.md"])
            self.assertEqual(warnings, ["duplicate slice_id sl-3; kept second--sl-3.md"])
            events = read_events(root)
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["record_id"], "sl-3")
            self.assertEqual(event["event_type"], "superseded")
            self.assertEqual(event["source"], "moc-reconcile")
            self.assertEqual(event["actor"], "moc-reconcile")
            self.assertEqual(event["reason"], "moc dedup")
            self.assertEqual(
                event["metadata"],
                {
                    "deleted_path": str(older),
                    "kept_path": str(newer),
                    "schema_version": "1",
                },
            )
            # sl-3 has no *state* event (the dedup trace is audit-only and is
            # skipped by the fold), so it stays default-active for retrieval...
            self.assertEqual(retrieval_set.active_records(root, ["sl-3"]), ["sl-3"])
            # ...but the audit-only trace MUST NOT establish lifecycle state, so
            # a slice with no prior lifecycle reports "unknown" (#184 review).
            self.assertEqual(retrieval_set.record_state(root, "sl-3"), "unknown")

    def test_dedup_lifecycle_event_uses_injected_now(self):
        # #184 review: the moc pass injects a logical `now`; the dedup ledger
        # trace must stamp it (no wall-clock) so the ledger stays deterministic.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge" / "paulshaclaw").mkdir(parents=True)
            _write(root, "first--sl-7.md", "sl-7", "OLD\n", title="first")
            time.sleep(0.01)
            _write(root, "second--sl-7.md", "sl-7", "NEW\n", title="second")
            fixed_now = "2000-01-01T00:00:00Z"
            naming.reconcile(root, fixed_now)
            events = read_events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["ts"], fixed_now)
            self.assertTrue(events[0]["event_id"].startswith(fixed_now))

    def test_skips_moc_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            moc = root / "knowledge" / "wiki-moc.md"
            moc.parent.mkdir(parents=True)
            moc.write_text("---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n", encoding="utf-8")
            naming.reconcile(root)
            self.assertTrue(moc.exists())  # untouched

    def test_rename_collision_keeps_newest_mtime(self):
        """When file renames to existing target with same slice_id, keep newest by mtime."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            # Older file needs to rename to target
            older = kdir / "old-name--sl-9.md"
            older.write_text("---\nslice_id: sl-9\nmemory_layer: knowledge\nproject: paulshaclaw\nartifact_kind: research\ntitle: target\n---\nOLD\n", encoding="utf-8")
            time.sleep(0.01)
            # Newer file already has target name
            newer = kdir / "target--sl-9.md"
            newer.write_text("---\nslice_id: sl-9\nmemory_layer: knowledge\nproject: paulshaclaw\nartifact_kind: research\ntitle: target\n---\nNEW\n", encoding="utf-8")
            naming.reconcile(root)
            target = kdir / "target--sl-9.md"
            self.assertTrue(target.exists())
            content = target.read_text(encoding="utf-8")
            self.assertIn("NEW", content, "Should keep newer file's content")
            self.assertNotIn("OLD", content)
            events = read_events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "superseded")
            self.assertEqual(
                events[0]["metadata"],
                {
                    "deleted_path": str(older),
                    "kept_path": str(target),
                    "schema_version": "1",
                },
            )

    def test_rename_collision_replaces_older_target_and_records_lifecycle_event(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            target = kdir / "target--sl-10.md"
            target.write_text(
                "---\nslice_id: sl-10\nmemory_layer: knowledge\nproject: paulshaclaw\nartifact_kind: research\ntitle: target\n---\nOLD\n",
                encoding="utf-8",
            )
            time.sleep(0.01)
            source = kdir / "old-name--sl-10.md"
            source.write_text(
                "---\nslice_id: sl-10\nmemory_layer: knowledge\nproject: paulshaclaw\nartifact_kind: research\ntitle: target\n---\nNEW\n",
                encoding="utf-8",
            )

            naming.reconcile(root)

            self.assertTrue(target.exists())
            self.assertFalse(source.exists())
            self.assertEqual(
                target.read_text(encoding="utf-8").splitlines()[-1],
                "NEW",
            )
            events = read_events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["record_id"], "sl-10")
            self.assertEqual(
                events[0]["metadata"],
                {
                    "deleted_path": str(target),
                    "kept_path": str(source),
                    "schema_version": "1",
                },
            )

    def test_lifecycle_append_failure_does_not_abort_reconcile(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            _write(root, "first--sl-11.md", "sl-11", "OLD\n", title="first")
            time.sleep(0.01)
            newer = _write(root, "second--sl-11.md", "sl-11", "NEW\n", title="second")

            with mock.patch(
                "paulsha_hippo.ledger.lifecycle.append_event",
                side_effect=OSError("ledger down"),
            ):
                warnings = naming.reconcile(root)

            self.assertEqual(warnings, ["duplicate slice_id sl-11; kept second--sl-11.md"])
            self.assertTrue(newer.exists())
            self.assertEqual(read_events(root), [])


if __name__ == "__main__":
    unittest.main()
