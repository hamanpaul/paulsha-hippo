from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import record_source
from paulsha_hippo.janitor import rules
from paulsha_hippo.janitor.config import JanitorConfig
from paulsha_hippo.janitor.record_source import KnowledgeRecord

CFG = JanitorConfig(schema_version="1", default_decay_age_days=90, by_artifact_kind={},
                    check_provenance_path=True, check_provenance_commit=False, decay_superseded=True)
HASH = "cfg-hash"
NOW = "2026-05-31T00:00:00Z"


def _rec(rid="sl-1", supersedes=(), source="claude:s1", captured="2026-01-01T00:00:00Z", path="docs/x.md"):
    return KnowledgeRecord(record_id=rid, supersedes=tuple(supersedes), source_key=source,
                           captured_at=captured, provenance={"repo": "r", "commit": "c", "path": path}, path=Path("/tmp/x.md"))


# default: path exists, so source_invalid never fires unless overridden
_PATH_OK = lambda rec: True


class DecayRuleTests(unittest.TestCase):
    def test_ttl_expired_decays(self):
        events = rules.plan_scan([_rec(captured="2020-01-01T00:00:00Z")], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "decayed")
        self.assertEqual(events[0]["reason"], "ttl_expired")
        self.assertEqual(events[0]["ts"], NOW)

    def test_fresh_record_stays_active(self):
        events = rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_superseded_beats_ttl(self):
        # sl-1 is old (would TTL) AND superseded by sl-7 -> reason must be superseded
        recs = [_rec(rid="sl-1", captured="2020-01-01T00:00:00Z"),
                _rec(rid="sl-7", supersedes=("sl-1",), captured="2026-05-30T00:00:00Z")]
        events = rules.plan_scan(recs, {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        decayed = [e for e in events if e["record_id"] == "sl-1"]
        self.assertEqual(decayed[0]["reason"], "superseded")
        self.assertEqual(decayed[0]["detail"]["superseded_by"], "sl-7")

    def test_source_invalid_when_path_missing(self):
        events = rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG, NOW, HASH,
                                 source_path_exists=lambda rec: False)
        self.assertEqual(events[0]["reason"], "source_invalid")

    def test_source_unknown_does_not_decay_source(self):
        # path unknown (None) -> fail-safe, no source_invalid; record is fresh -> stays active
        events = rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG, NOW, HASH,
                                 source_path_exists=lambda rec: None)
        self.assertEqual(events, [])

    def test_self_supersession_does_not_decay_record(self):
        rec = _rec(rid="sl-1", supersedes=("sl-1",), captured="2026-05-30T00:00:00Z")
        events = rules.plan_scan([rec], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_already_decayed_record_not_redecayed(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-04-01T00:00:00Z"}}
        events = rules.plan_scan([_rec(captured="2020-01-01T00:00:00Z")], {}, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])  # decayed records only evaluate reactivation

    def test_decayed_event_carries_original_ref(self):
        events = rules.plan_scan([_rec(captured="2020-01-01T00:00:00Z")], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events[0]["original_ref"]["slice_id"], "sl-1")
        self.assertEqual(events[0]["original_ref"]["source_key"], "claude:s1")


class ReactivationRuleTests(unittest.TestCase):
    def test_reimport_after_decay_reactivates(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-03-01T00:00:00Z"}}
        import_index = {"claude:s1": [{"status": "updated", "recorded_at": "2026-04-01T00:00:00Z"}]}
        events = rules.plan_scan([_rec()], import_index, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events[0]["event_type"], "reactivation")
        self.assertEqual(events[0]["agent_ref"], "claude:s1")
        self.assertEqual(events[0]["detail"]["import_ts"], "2026-04-01T00:00:00Z")

    def test_reimport_before_decay_does_not_reactivate(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-05-01T00:00:00Z"}}
        import_index = {"claude:s1": [{"status": "written", "recorded_at": "2026-01-01T00:00:00Z"}]}
        events = rules.plan_scan([_rec()], import_index, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_no_import_does_not_reactivate(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-03-01T00:00:00Z"}}
        events = rules.plan_scan([_rec()], {}, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_same_instant_mixed_timestamp_formats_do_not_reactivate(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-03-01T00:00:00.123456+00:00"}}
        import_index = {"claude:s1": [{"status": "updated", "recorded_at": "2026-03-01T00:00:00Z"}]}
        events = rules.plan_scan([_rec()], import_index, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_unknown_source_key_does_not_reactivate(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-03-01T00:00:00Z"}}
        import_index = {"claude:_unknown": [{"status": "updated", "recorded_at": "2026-04-01T00:00:00Z"}]}
        events = rules.plan_scan([_rec(source="claude:_unknown")], import_index, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_anti_flap_reactivated_record_not_immediately_redecayed(self):
        # old captured_at, but reactivated yesterday -> TTL base = reactivation ts -> stays active
        lc_state = {"sl-1": {"state": "active", "since_ts": "2026-05-30T00:00:00Z"}}
        events = rules.plan_scan([_rec(captured="2020-01-01T00:00:00Z")], {}, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])

    def test_malformed_import_index_does_not_crash_or_reactivate(self):
        lc_state = {"sl-1": {"state": "decayed", "since_ts": "2026-03-01T00:00:00Z"}}
        import_index = {"claude:s1": "not-a-list"}
        events = rules.plan_scan([_rec()], import_index, lc_state, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_same_plan(self):
        recs = [_rec(captured="2020-01-01T00:00:00Z")]
        a = rules.plan_scan(recs, {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        b = rules.plan_scan(recs, {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(a, b)

    def test_multiple_supersessions_are_input_order_independent(self):
        old = _rec(rid="sl-1", captured="2020-01-01T00:00:00Z")
        newer_a = _rec(rid="sl-7", supersedes=("sl-1",), captured="2026-05-30T00:00:00Z")
        newer_b = _rec(rid="sl-8", supersedes=("sl-1",), captured="2026-05-30T00:00:00Z")
        a = rules.plan_scan([newer_b, old, newer_a], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        b = rules.plan_scan([newer_a, old, newer_b], {}, {}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(a, b)
        self.assertEqual(a[0]["detail"]["superseded_by"], "sl-7")

    def test_malformed_lifecycle_state_does_not_crash(self):
        events = rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {"sl-1": "not-a-dict"}, CFG, NOW, HASH, source_path_exists=_PATH_OK)
        self.assertEqual(events, [])


def _lint_rec(rid="sl-1", title="真標題", project="paulshaclaw"):
    return KnowledgeRecord(
        record_id=rid,
        supersedes=(),
        source_key="claude:s1",
        captured_at="2026-06-01T00:00:00Z",
        provenance={"repo": "r", "commit": "c", "path": "docs/x.md"},
        path=Path("/tmp/x.md"),
        title=title,
        project=project,
    )


class LintRuleTests(unittest.TestCase):
    def test_untitled_title_is_flagged(self):
        findings = rules.plan_lint([_lint_rec(rid="sl-u1", title="untitled")])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "title-untitled")
        self.assertEqual(findings[0]["record_id"], "sl-u1")

    def test_remote_form_project_id_is_not_flagged(self):
        findings = rules.plan_lint([_lint_rec(rid="sl-r1", project="github.com/hamanpaul/testpilot")])
        self.assertEqual(findings, [])

    def test_clean_record_yields_no_findings(self):
        self.assertEqual(rules.plan_lint([_lint_rec()]), [])

    def test_remote_form_project_with_untitled_only_flags_title(self):
        findings = rules.plan_lint([_lint_rec(title="untitled", project="a/b")])
        self.assertEqual({finding["rule"] for finding in findings}, {"title-untitled"})

    def test_findings_are_deterministic_and_sorted(self):
        first = _lint_rec(rid="sl-2", title="untitled")
        second = _lint_rec(rid="sl-1", title="untitled")
        findings_a = rules.plan_lint([first, second])
        findings_b = rules.plan_lint([second, first])
        self.assertEqual(findings_a, findings_b)
        self.assertEqual([finding["record_id"] for finding in findings_a], ["sl-1", "sl-2"])


class LintFieldExtractionTests(unittest.TestCase):
    def test_iter_records_extracts_title_and_project(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            kroot.mkdir(parents=True)
            (kroot / "untitled--sl-1.md").write_text(
                "---\nmemory_layer: knowledge\nslice_id: sl-1\ntitle: untitled\n"
                "project: github.com/hamanpaul/testpilot\nsource_agent: claude\n"
                'source_session: s1\ncaptured_at: "2026-06-22T00:00:00Z"\n---\nbody\n',
                encoding="utf-8",
            )

            records, warnings = record_source.iter_records(kroot)

            self.assertEqual(warnings, [])
            self.assertEqual(records[0].title, "untitled")
            self.assertEqual(records[0].project, "github.com/hamanpaul/testpilot")

    def test_missing_fields_default_to_empty(self):
        record = _rec()
        self.assertEqual(record.title, "")
        self.assertEqual(record.project, "")
        self.assertEqual(rules.plan_lint([record]), [])


if __name__ == "__main__":
    unittest.main()
