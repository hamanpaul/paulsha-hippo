from __future__ import annotations

import hashlib
import unittest

from paulsha_hippo.lib.lifecycle.schema import compute_checksum, parse_artifact_text, validate_frontmatter
from paulsha_hippo.atomizer import slice_frontmatter
from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.atomizer.llm_output import SliceProposal
from paulsha_hippo.atomizer.splitter import Fragment

CFG = AtomizerConfig(
    schema_version="1", boundary_patterns=(r"^#{1,6}\s",), max_fragment_chars=8000,
    artifact_kind_map={"research": "research", "session": "report"},
    phase_map={"research": "research", "report": "review"},
    default_artifact_kind="report", default_phase="review")


def _frag(source_artifact="research", index=0, body="alpha"):
    return Fragment(project="paulshaclaw", source_agent="claude", source_session="s1",
                    source_artifact=source_artifact, captured_at="2026-05-31T00:00:00Z",
                    provenance={"repo": "paulshaclaw", "commit": "c", "path": "docs/x.md"},
                    fragment_index=index, body=body)


_SESSION_META = {
    "source_agent": "claude",
    "source_session": "s1",
    "captured_at": "2026-06-02T00:00:00Z",
    "provenance": {"repo": "r", "commit": "c", "path": "p"},
}


def _proposal(body="distilled body"):
    return SliceProposal(
        title="alpha",
        artifact_kind="report",
        project="prplos-core",
        tags=("pwhm", "fsm"),
        body=body,
        source_fragment_indices=(0, 1),
        relations=({"type": "mentions", "entity": "MTK"},),
    )


class SliceFrontmatterTests(unittest.TestCase):
    def test_slice_id_deterministic(self):
        a = slice_frontmatter.build(_frag(), CFG)
        b = slice_frontmatter.build(_frag(), CFG)
        self.assertEqual(a.slice_id, b.slice_id)
        self.assertTrue(a.slice_id.startswith("sl-"))

    def test_slice_id_varies_by_fragment_index(self):
        self.assertNotEqual(slice_frontmatter.build(_frag(index=0), CFG).slice_id,
                            slice_frontmatter.build(_frag(index=1), CFG).slice_id)

    def test_artifact_kind_and_phase_mapping(self):
        s = slice_frontmatter.build(_frag(source_artifact="research"), CFG)
        self.assertEqual(s.frontmatter["artifact_kind"], "research")
        self.assertEqual(s.frontmatter["phase"], "research")

    def test_unknown_artifact_uses_defaults(self):
        s = slice_frontmatter.build(_frag(source_artifact="mystery"), CFG)
        self.assertEqual(s.frontmatter["artifact_kind"], "report")
        self.assertEqual(s.frontmatter["phase"], "review")

    def test_has_t4_contract_fields(self):
        fm = slice_frontmatter.build(_frag(), CFG).frontmatter
        for key in ("memory_layer", "source_agent", "captured_at", "provenance", "supersedes"):
            self.assertIn(key, fm)
        self.assertEqual(fm["memory_layer"], "knowledge")

    def test_checksum_matches_body(self):
        s = slice_frontmatter.build(_frag(body="hello body"), CFG)
        self.assertEqual(s.frontmatter["checksum"], compute_checksum(s.body))

    def test_passes_stage3_validation(self):
        s = slice_frontmatter.build(_frag(), CFG)
        result = validate_frontmatter(frontmatter=s.frontmatter, body=s.body)
        self.assertTrue(result.ok, result.errors)

    def test_serialized_slice_parses_and_validates(self):
        s = slice_frontmatter.build(_frag(), CFG)
        text = slice_frontmatter.render(s)
        doc = parse_artifact_text(text)
        result = validate_frontmatter(frontmatter=doc.frontmatter, body=doc.body)
        self.assertTrue(result.ok, result.errors)

    def test_validate_reports_t4_gap(self):
        fm = {"phase": "research"}  # missing everything
        errors = slice_frontmatter.validate(fm, "body")
        self.assertTrue(errors)


class BuildFromProposalTests(unittest.TestCase):
    def test_content_derived_slice_id(self):
        built = slice_frontmatter.build_from_proposal(_proposal("X"), _SESSION_META)
        expected = "sl-" + hashlib.sha256(
            ("claude|s1|" + hashlib.sha256(b"X").hexdigest()).encode("utf-8")
        ).hexdigest()[:16]
        self.assertEqual(built.slice_id, expected)

    def test_canonical_title_is_retained_in_frontmatter(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        self.assertEqual(built.title, "alpha")
        self.assertEqual(built.frontmatter["title"], "alpha")

    def test_union_frontmatter_with_tags(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        self.assertEqual(built.frontmatter["title"], "alpha")
        self.assertEqual(built.frontmatter["project"], "prplos-core")
        self.assertEqual(built.frontmatter["artifact_kind"], "report")
        self.assertEqual(built.frontmatter["tags"], ["pwhm", "fsm"])
        self.assertEqual(built.frontmatter["memory_layer"], "knowledge")
        self.assertEqual(built.frontmatter["source_fragments"], [0, 1])
        self.assertEqual(built.frontmatter["checksum"], compute_checksum(built.body))

    def test_passes_dual_validation(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        self.assertEqual(slice_frontmatter.validate(built.frontmatter, built.body), [])
        self.assertTrue(validate_frontmatter(frontmatter=built.frontmatter, body=built.body).ok)

    def test_relations_attached(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        self.assertEqual(built.relations[0]["entity"], "MTK")

    def test_relations_tuple_is_reused_without_copy(self):
        proposal = _proposal()
        built = slice_frontmatter.build_from_proposal(proposal, _SESSION_META)
        self.assertIs(built.relations, proposal.relations)

    def test_proposal_phase_mapping_matches_plan(self):
        cases = {
            "research": "research",
            "spec": "define",
            "plan": "plan",
            "report": "review",
            "review": "review",
            "task": "review",
            "roadmap": "review",
        }
        for artifact_kind, expected_phase in cases.items():
            with self.subTest(artifact_kind=artifact_kind):
                built = slice_frontmatter.build_from_proposal(
                    SliceProposal(
                        title="alpha",
                        artifact_kind=artifact_kind,
                        project="prplos-core",
                        tags=("pwhm",),
                        body="distilled body",
                        source_fragment_indices=(0,),
                        relations=(),
                    ),
                    _SESSION_META,
                )
                self.assertEqual(built.frontmatter["phase"], expected_phase)

    def test_render_serializes_list_fields_as_json_arrays(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        rendered = slice_frontmatter.render(built)
        self.assertIn('tags: ["pwhm", "fsm"]', rendered)
        self.assertIn("source_fragments: [0, 1]", rendered)
        self.assertIn('\ntitle: "alpha"', rendered)

    def test_legacy_build_leaves_runtime_title_unset(self):
        built = slice_frontmatter.build(_frag(), CFG)
        self.assertIsNone(built.title)

    def test_rendered_proposal_round_trip_preserves_list_fields(self):
        built = slice_frontmatter.build_from_proposal(_proposal(), _SESSION_META)
        parsed = parse_artifact_text(slice_frontmatter.render(built))
        self.assertEqual(parsed.frontmatter["tags"], ["pwhm", "fsm"])
        self.assertEqual(parsed.frontmatter["source_fragments"], [0, 1])

    def test_rendered_proposal_round_trip_preserves_tag_punctuation(self):
        proposal = SliceProposal(
            title="alpha",
            artifact_kind="report",
            project="prplos-core",
            tags=('router, wifi', 'quote"tag', "bracket[tag]"),
            body="distilled body",
            source_fragment_indices=(0, 1),
            relations=(),
        )
        built = slice_frontmatter.build_from_proposal(proposal, _SESSION_META)
        parsed = parse_artifact_text(slice_frontmatter.render(built))
        self.assertEqual(parsed.frontmatter["tags"], ['router, wifi', 'quote"tag', "bracket[tag]"])

    def test_parse_artifact_text_does_not_coerce_invalid_source_fragments(self):
        text = (
            "---\n"
            "phase: review\n"
            "project: paulshaclaw\n"
            "slice_id: sl-1\n"
            "artifact_kind: report\n"
            "version: 1\n"
            "created_at: 2026-06-02T00:00:00Z\n"
            "created_by: claude\n"
            "source_session: s1\n"
            "gate_required: false\n"
            "checksum: deadbeef\n"
            "source_fragments: [2.9, true]\n"
            "---\n"
            "body"
        )
        parsed = parse_artifact_text(text)
        self.assertEqual(parsed.frontmatter["source_fragments"], "[2.9, true]")

    def test_parse_artifact_text_preserves_legacy_single_quoted_tags(self):
        text = (
            "---\n"
            "phase: review\n"
            "project: paulshaclaw\n"
            "slice_id: sl-1\n"
            "artifact_kind: report\n"
            "version: 1\n"
            "created_at: 2026-06-02T00:00:00Z\n"
            "created_by: claude\n"
            "source_session: s1\n"
            "gate_required: false\n"
            "checksum: deadbeef\n"
            "tags: ['pwhm', 'fsm']\n"
            "---\n"
            "body"
        )
        parsed = parse_artifact_text(text)
        self.assertEqual(parsed.frontmatter["tags"], ["pwhm", "fsm"])


class BuildFromProposalTitleTests(unittest.TestCase):
    def _proposal(self) -> SliceProposal:
        return SliceProposal(
            title="atom one", artifact_kind="report", project="paulshaclaw",
            tags=("t1",), body="distilled body", source_fragment_indices=(0,), relations=(),
        )

    def _meta(self) -> dict:
        return {"source_agent": "claude", "source_session": "s1",
                "captured_at": "2026-01-01T00:00:00Z", "provenance": {},
                "session_title": "修正 X 啟動"}

    def test_titles_persisted_to_frontmatter(self):
        sl = slice_frontmatter.build_from_proposal(self._proposal(), self._meta())
        self.assertEqual(sl.frontmatter["session_title"], "修正 X 啟動")
        self.assertEqual(sl.frontmatter["atom_title"], "atom one")

    def test_titles_rendered_quoted(self):
        rendered = slice_frontmatter.render(
            slice_frontmatter.build_from_proposal(self._proposal(), self._meta()))
        self.assertIn('session_title: "修正 X 啟動"', rendered)
        self.assertIn('atom_title: "atom one"', rendered)

    def test_missing_session_title_defaults_empty(self):
        meta = self._meta(); del meta["session_title"]
        sl = slice_frontmatter.build_from_proposal(self._proposal(), meta)
        self.assertEqual(sl.frontmatter["session_title"], "")


if __name__ == "__main__":
    unittest.main()
