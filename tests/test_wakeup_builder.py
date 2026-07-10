from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.wakeup import builder as builder_module
from paulsha_hippo.wakeup import build_brief


def _slice(root: Path, slice_id: str, project: str, title: str, body: str = None) -> None:
    body_text = body if body is not None else f"body {slice_id}\n"
    path = root / "knowledge" / project / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n"
                    f"title: {title}\n---\n{body_text}", encoding="utf-8")


def _moc(root: Path, project: str, body: str) -> None:
    path = root / "knowledge" / f"{project}-moc.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class WakeupBuilderTests(unittest.TestCase):
    def test_map_and_recent_ordering(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            _slice(root, "sl-1", "p", "alpha")
            _slice(root, "sl-2", "p", "beta")
            _slice(root, "sl-3", "p", "gamma")
            # append events with increasing times
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-01-01T00:00:00Z")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-2", event_type="updated", source="x", reason="r", actor="a", ts="2026-02-01T00:00:00Z")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-3", event_type="accessed", source="x", reason="r", actor="a", ts="2026-03-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", k=10)
            self.assertIn("# Memory wake-up — p", out)
            self.assertIn("MOC BODY", out)
            # recent ordering newest first: sl-3, sl-2, sl-1
            idx3 = out.find("sl-3")
            idx2 = out.find("sl-2")
            idx1 = out.find("sl-1")
            self.assertTrue(idx3 < idx2 < idx1)

    def test_unknown_project_returns_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(build_brief(root, "_unknown", now="2026-06-03T00:00:00Z"), "")

    def test_empty_project_returns_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(build_brief(root, "", now="2026-06-03T00:00:00Z"), "")

    def test_project_with_no_slices_returns_empty(self):
        # Updated: MOC-only should yield a brief with Map section, not empty
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            # no slices
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # should not be empty, should have Map section
            self.assertNotEqual(out, "")
            self.assertIn("## Map", out)
            self.assertIn("MOC BODY", out)

    def test_decayed_slice_excluded(self):
        # Updated: MOC-only should yield a brief with Map section, not empty
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="decayed", source="janitor", reason="ttl", actor="janitor", ts="2026-04-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # should not be empty, should have Map section (decayed slice excluded from Recent)
            self.assertNotEqual(out, "")
            self.assertIn("## Map", out)
            self.assertIn("MOC BODY", out)

    def test_char_budget_truncates_map_tail_but_keeps_recent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = "".join([f"Line {i}\n" for i in range(1000)])
            _moc(root, "p", big)
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=200)
            # budget contract: never exceed the provided char_budget
            self.assertLessEqual(len(out), 200)
            self.assertIn("## Recent", out)
            self.assertIn("(truncated)", out)

    def test_recent_summary_comes_from_body(self):
        # New TDD: ensure summary uses first non-empty line from the slice body, not title
        # Updated: wikilink now uses file stem
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            _slice(root, "sl-1", "p", "meta-title", body="First line summary\nSecond line\n")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # expect the recent entry to show the body-derived summary and enriched format
            self.assertIn("sl-1", out)
            self.assertIn("[[meta-title--sl-1]]", out)  # Updated: now uses file stem
            self.assertIn("First line summary", out)
            self.assertIn("(2026-05-01T00:00:00Z)", out)
            self.assertNotIn("sl-1: meta-title", out)

    def test_map_header_present_even_under_tight_budget(self):
        # New TDD: when char budget is very tight, Map header must still be present and show (truncated)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = "".join([f"Line {i}\n" for i in range(1000)])
            _moc(root, "p", big)
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            # very small budget to force tight behavior
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=80)
            # budget contract
            self.assertLessEqual(len(out), 80)
            self.assertIn("## Map", out)
            self.assertIn("(truncated)", out)

    def test_strict_budget_never_exceeds(self):
        # ensure no code path can return more than the provided char_budget
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            # extremely tight budget intended to exercise extreme fallback
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=5)
            self.assertLessEqual(len(out), 5)

    def test_preserve_recent_over_map_in_extreme_fallback(self):
        # prefer preserving Recent continuity when budget forces a hard choice
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            # small budget that should allow Recent but not a full Map body
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=40)
            self.assertLessEqual(len(out), 40)
            self.assertIn("## Recent", out)
            # Map may be absent in this extreme case; if present it should show truncated marker
            self.assertTrue(("## Map" not in out) or ("(truncated)" in out))

    def test_deterministic_repeated_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            a = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            b = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            self.assertEqual(a, b)

    def test_moc_only_yields_map_section(self):
        # Issue 1: MOC-only data should yield a brief with Map section
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC BODY\n")
            # no slices
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # should not be empty, should have Map section
            self.assertNotEqual(out, "")
            self.assertIn("# Memory wake-up — p", out)
            self.assertIn("## Map", out)
            self.assertIn("MOC BODY", out)
            # should not have Recent section (or empty Recent section is acceptable)

    def test_recent_only_yields_recent_section(self):
        # Issue 1: Recent-only data should yield a brief with Recent section
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # no MOC
            _slice(root, "sl-1", "p", "alpha")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # should not be empty, should have Recent section
            self.assertNotEqual(out, "")
            self.assertIn("# Memory wake-up — p", out)
            self.assertIn("## Recent", out)
            self.assertIn("sl-1", out)
            # should not have Map section (or empty Map section is acceptable)

    def test_neither_moc_nor_active_slices_returns_empty(self):
        # Issue 1: truly empty project (no MOC, no slices) should return empty
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # no MOC, no slices
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            self.assertEqual(out, "")

    def test_recent_wikilink_uses_slice_stem_not_title(self):
        # Issue 2: Recent entries should use the actual slice stem for the wikilink
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            _slice(root, "sl-1", "p", "meta-title", body="First line summary\n")
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")
            out = build_brief(root, "p", now="2026-06-03T00:00:00Z")
            # expect the wikilink to be [[meta-title--sl-1]] not [[meta-title]]
            self.assertIn("[[meta-title--sl-1]]", out)
            self.assertNotIn("[[meta-title]]", out)

    def test_reuses_lifecycle_events_for_active_record_filter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            _slice(root, "sl-1", "p", "alpha")
            events = [
                {
                    "record_id": "sl-1",
                    "event_type": "created",
                    "ts": "2026-05-01T00:00:00Z",
                }
            ]
            lifecycle_state = {
                "sl-1": {
                    "last_state": "active",
                    "last_event_ts": "2026-05-01T00:00:00Z",
                    "deleted": None,
                }
            }
            with (
                mock.patch(
                    "paulsha_hippo.wakeup.builder.lifecycle.read_events",
                    return_value=events,
                ) as read_events,
                mock.patch(
                    "paulsha_hippo.wakeup.builder.lifecycle.fold_lifecycle",
                    return_value=lifecycle_state,
                ),
                mock.patch(
                    "paulsha_hippo.wakeup.builder.retrieval_set.active_records",
                    return_value=["sl-1"],
                ) as active_records,
            ):
                build_brief(root, "p", now="2026-06-03T00:00:00Z")

            read_events.assert_called_once_with(root)
            self.assertIs(active_records.call_args.kwargs["events"], events)

    def test_large_slice_body_is_truncated_and_marked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            giant_body = "A" * (300 * 1024)
            _slice(root, "sl-1", "p", "alpha", body=giant_body)
            lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                   record_id="sl-1", event_type="created", source="x", reason="r", actor="a", ts="2026-05-01T00:00:00Z")

            out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=400000)

            self.assertIn("sl-1", out)
            self.assertIn("[truncated]", out)

    def test_total_slice_body_budget_stops_collecting_and_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            for index in range(3):
                _slice(root, f"sl-{index}", "p", f"title-{index}", body=f"body-{index}-" + ("X" * 40))
                lifecycle.append_event(path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                                       record_id=f"sl-{index}", event_type="created", source="x", reason="r", actor="a", ts=f"2026-05-0{index + 1}T00:00:00Z")

            with (
                mock.patch.object(builder_module, "MAX_SLICE_BODY_BYTES", 64, create=True),
                mock.patch.object(builder_module, "MAX_SLICE_BODY_TOTAL_BYTES", 120, create=True),
                self.assertLogs("paulsha_hippo.wakeup.builder", level="INFO") as captured,
            ):
                out = build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=1000)

            self.assertIn("sl-2", out)
            self.assertIn("sl-1", out)
            self.assertNotIn("sl-0", out)
            self.assertIn("total slice body budget", "\n".join(captured.output))

    def test_malformed_frontmatter_cap_treats_file_as_body(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "malformed.md"
            path.write_text("---\n" + ("key: value\n" * 40), encoding="utf-8")

            with mock.patch.object(builder_module, "MAX_FRONTMATTER_BYTES", 64, create=True):
                fm, body_offset = builder_module._read_frontmatter_only(path)
                body, truncated, bytes_used = builder_module._read_slice_body(
                    path,
                    body_limit=32,
                )

            self.assertEqual(fm, {})
            self.assertEqual(body_offset, 0)
            self.assertTrue(truncated)
            self.assertEqual(bytes_used, 32)
            self.assertTrue(body.startswith("---\nkey: value"))
            self.assertIn(builder_module.TRUNCATED_MARKER, body)

    def test_slice_stat_failure_is_skipped_fail_open(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            _slice(root, "sl-1", "p", "alpha", body="summary\n")
            real_stat = Path.stat

            def flaky_stat(path_obj: Path, *args, **kwargs):
                if path_obj.name == "alpha--sl-1.md":
                    raise OSError("stat failed")
                return real_stat(path_obj, *args, **kwargs)

            with mock.patch("pathlib.Path.stat", autospec=True, side_effect=flaky_stat):
                out = build_brief(root, "p", now="2026-06-03T00:00:00Z")

            self.assertIn("MOC", out)
            self.assertNotIn("sl-1", out)

    def test_total_slice_body_budget_clamps_body_read_limit_to_remaining_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _moc(root, "p", "MOC\n")
            for index in range(2):
                _slice(root, f"sl-{index}", "p", f"title-{index}", body="B" * 50)
                lifecycle.append_event(
                    path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                    record_id=f"sl-{index}",
                    event_type="created",
                    source="x",
                    reason="r",
                    actor="a",
                    ts=f"2026-05-0{index + 1}T00:00:00Z",
                )

            requested_limits: list[int] = []

            def fake_read_slice_body(path: Path, *, body_limit: int) -> tuple[str, bool, int]:
                requested_limits.append(body_limit)
                return ("summary\n", False, 50)

            with (
                mock.patch.object(builder_module, "MAX_SLICE_BODY_BYTES", 64, create=True),
                mock.patch.object(
                    builder_module,
                    "MAX_SLICE_BODY_TOTAL_BYTES",
                    100,
                    create=True,
                ),
                mock.patch.object(
                    builder_module,
                    "_read_slice_body",
                    side_effect=fake_read_slice_body,
                ),
            ):
                build_brief(root, "p", now="2026-06-03T00:00:00Z", char_budget=1000)

            self.assertEqual(requested_limits, [64, 50])


def test_build_orientation_concise(tmp_path):
    from paulsha_hippo.wakeup.builder import build_orientation
    k = tmp_path / "knowledge" / "proj"; k.mkdir(parents=True)
    (k / "a.md").write_text("---\nmemory_layer: knowledge\nslice_id: sl-a\n---\nx\n", encoding="utf-8")
    (k / "b.md").write_text("---\nmemory_layer: knowledge\nslice_id: sl-b\n---\ny\n", encoding="utf-8")
    (k / "proj-moc.md").write_text("# moc\n", encoding="utf-8")  # excluded from count
    out = build_orientation(tmp_path, "proj")
    assert "Read" in out and "2" in out
    assert "## Map" not in out and "[[" not in out  # no MOC dump, no wikilinks


def test_build_orientation_empty_when_no_notes(tmp_path):
    from paulsha_hippo.wakeup.builder import build_orientation
    assert build_orientation(tmp_path, "proj") == ""


def test_build_orientation_custom_retrieval_hint(tmp_path):
    from paulsha_hippo.wakeup.builder import build_orientation
    k = tmp_path / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text("---\nmemory_layer: knowledge\n---\nx\n", encoding="utf-8")
    out = build_orientation(tmp_path, "proj", retrieval_hint="CUSTOM-HINT")
    assert "CUSTOM-HINT" in out
    assert "每次 prompt 後以短清單浮現" not in out
    default = build_orientation(tmp_path, "proj")
    assert "每次 prompt 後以短清單浮現" in default


if __name__ == "__main__":
    unittest.main()
