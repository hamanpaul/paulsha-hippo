from __future__ import annotations

import json
import unittest

from paulsha_hippo.atomizer import llm_output

PROJECTS = ["paulshaclaw", "prplos-core"]
WRAPPER_KEYS = ("findings", "slices", "proposals", "atoms")
GOOD = (
    'prose before\n```json\n'
    '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":["t"],'
    '"body":"b","source_fragment_indices":[0],"relations":[]}]\n```\nprose after'
)


def _proposal_payload(
    title: str,
    *,
    artifact_kind: str = "report",
    project: str = "paulshaclaw",
    fragment_index: int = 0,
) -> dict[str, object]:
    return {
        "title": title,
        "artifact_kind": artifact_kind,
        "project": project,
        "tags": ["t"],
        "body": f"body-{title}",
        "source_fragment_indices": [fragment_index],
        "relations": [],
    }


class LlmOutputTests(unittest.TestCase):
    def test_parses_fenced_json(self):
        proposals = llm_output.parse(GOOD, PROJECTS)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].artifact_kind, "report")
        self.assertEqual(proposals[0].project, "paulshaclaw")

    def test_parses_bare_array(self):
        raw = (
            '[{"title":"a","artifact_kind":"plan","project":"_unknown","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        self.assertEqual(len(llm_output.parse(raw, PROJECTS)), 1)

    def test_parses_object_wrapped_whitelisted_array(self):
        for key in WRAPPER_KEYS:
            with self.subTest(key=key):
                raw = json.dumps({key: [_proposal_payload("a"), _proposal_payload("b", fragment_index=1)]})
                proposals = llm_output.parse(raw, PROJECTS)
                self.assertEqual([proposal.title for proposal in proposals], ["a", "b"])

    def test_object_wrapped_empty_array_returns_no_proposals(self):
        for key in WRAPPER_KEYS:
            with self.subTest(key=key):
                self.assertEqual(llm_output.parse(json.dumps({key: []}), PROJECTS), [])

    def test_empty_array_returns_no_proposals(self):
        self.assertEqual(llm_output.parse("[]", PROJECTS), [])

    def test_fenced_empty_array_with_reasoning_returns_no_proposals(self):
        raw = "```json\n[]\n```\n**Reasoning:** The session contains no substantive content."
        self.assertEqual(llm_output.parse(raw, PROJECTS), [])

    def test_all_invalid_proposals_still_raise_no_salvageable(self):
        with self.assertRaisesRegex(llm_output.LlmOutputError, "no salvageable proposals"):
            llm_output.parse('[{"bogus": 1}]', PROJECTS)

    def test_parses_bare_array_after_label_on_previous_line(self):
        raw = (
            "Output:\n"
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        self.assertEqual(len(llm_output.parse(raw, PROJECTS)), 1)

    def test_parses_bare_array_after_label_on_same_line(self):
        raw = (
            'Output: [{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        self.assertEqual(len(llm_output.parse(raw, PROJECTS)), 1)

    def test_same_line_non_output_label_does_not_count_as_payload(self):
        raw = (
            'Example: [{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        with self.assertRaisesRegex(llm_output.LlmOutputError, "no JSON array found"):
            llm_output.parse(raw, PROJECTS)

    def test_malformed_json_raises(self):
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse("not json at all", PROJECTS)

    def test_bad_artifact_kind_raises(self):
        raw = (
            '[{"title":"a","artifact_kind":"banana","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse(raw, PROJECTS)

    def test_unknown_project_is_coerced_to_unknown(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"ghost","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(proposals[0].project, "_unknown")

    def test_unsafe_project_path_is_coerced_to_unknown(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"../escaped","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        # an unsafe value never reaches the filesystem: it is coerced, not trusted
        proposals = llm_output.parse(raw, ["../escaped"])
        self.assertEqual(proposals[0].project, "_unknown")

    def test_empty_body_raises(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"  ",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse(raw, PROJECTS)

    def test_missing_required_fields_raise(self):
        cases = (
            ('[{"artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","source_fragment_indices":[0],"relations":[]}]'),
            ('[{"title":"a","artifact_kind":"report","project":"paulshaclaw","body":"b","source_fragment_indices":[0],"relations":[]}]'),
            ('[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","relations":[]}]'),
            ('[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","source_fragment_indices":[0]}]'),
        )
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(llm_output.LlmOutputError):
                    llm_output.parse(raw, PROJECTS)

    def test_title_must_be_non_empty_string(self):
        cases = (
            '[{"title":"","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","source_fragment_indices":[0],"relations":[]}]',
            '[{"title":"   ","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","source_fragment_indices":[0],"relations":[]}]',
            '[{"title":7,"artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b","source_fragment_indices":[0],"relations":[]}]',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(llm_output.LlmOutputError):
                    llm_output.parse(raw, PROJECTS)

    def test_tag_entries_must_be_strings(self):
        cases = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[7],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[true],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[null],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(llm_output.LlmOutputError):
                    llm_output.parse(raw, PROJECTS)

    def test_source_fragment_indices_must_be_ints_and_not_bools(self):
        cases = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":["1"],"relations":[]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[2.9],"relations":[]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[true],"relations":[]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[false],"relations":[]}]',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(llm_output.LlmOutputError):
                    llm_output.parse(raw, PROJECTS)

    def test_empty_source_fragment_indices_raise(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[],"relations":[]}]'
        )
        with self.assertRaisesRegex(
            llm_output.LlmOutputError, "proposal 0 source_fragment_indices must not be empty"
        ):
            llm_output.parse(raw, PROJECTS)

    def test_parses_bare_array_with_non_json_brackets_in_prose(self):
        raw = (
            'analysis [draft only]\n'
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]\n'
            'done ] trailing note'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(len(proposals), 1)

    def test_duplicate_title_drops_later_keeps_first(self):
        raw = (
            '[{"title":"dup","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b1",'
            '"source_fragment_indices":[0],"relations":[]},'
            '{"title":"dup","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b2",'
            '"source_fragment_indices":[1],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].body, "b1")

    def test_non_list_fields_raise(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":"x","body":"b",'
            '"source_fragment_indices":"0","relations":{}}]'
        )
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse(raw, PROJECTS)

    def test_unknown_top_level_fields_raise(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[],"extra":"x"}]'
        )
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse(raw, PROJECTS)

    def test_non_object_relation_is_dropped(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":["bad"]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(proposals[0].relations, ())

    def test_dangling_relates_to_parses_for_later_resolution(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"relates_to","target_title":"missing"}]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(proposals[0].relations, ({"type": "relates_to", "target_title": "missing"},))

    def test_parser_normalizes_whitespace_in_titles_and_relations(self):
        raw = (
            '[{"title":"  a  ","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":['
            '{"type":"relates_to","target_title":"  other  "},'
            '{"type":"mentions","entity":"  MTK  "}]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(proposals[0].title, "a")
        self.assertEqual(
            proposals[0].relations,
            (
                {"type": "relates_to", "target_title": "other"},
                {"type": "mentions", "entity": "MTK"},
            ),
        )

    def test_malformed_relates_to_relation_is_dropped(self):
        cases = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"relates_to"}]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"relates_to","target_title":"  "}]}]',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                proposals = llm_output.parse(raw, PROJECTS)
                self.assertEqual(proposals[0].relations, ())

    def test_malformed_mentions_relation_is_dropped(self):
        cases = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"mentions"}]}]',
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"mentions","entity":"  "}]}]',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                proposals = llm_output.parse(raw, PROJECTS)
                self.assertEqual(proposals[0].relations, ())

    def test_unknown_relation_type_is_dropped(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[{"type":"unknown","target_title":"x"}]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(proposals[0].relations, ())

    def test_relation_extra_fields_dropped_proposal_survives(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body a",'
            '"source_fragment_indices":[0],"relations":[{"type":"mentions","entity":"MTK","extra":"x"}]},'
            '{"title":"b","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body b",'
            '"source_fragment_indices":[1],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual([p.title for p in proposals], ["a", "b"])
        self.assertEqual(proposals[0].relations, ())

    def test_skips_syntactically_valid_non_proposal_array(self):
        raw = (
            'analysis scratch: [1, 2, 3]\n'
            'actual payload follows\n'
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].title, "a")

    def test_multiple_valid_proposal_arrays_raise(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]\n'
            '[{"title":"c","artifact_kind":"plan","project":"prplos-core","tags":[],"body":"d",'
            '"source_fragment_indices":[1],"relations":[]}]'
        )
        with self.assertRaisesRegex(llm_output.LlmOutputError, "multiple valid JSON arrays found"):
            llm_output.parse(raw, PROJECTS)

    def test_wrapped_array_and_bare_array_still_raise_multiple_valid_arrays(self):
        raw = (
            json.dumps({"findings": [_proposal_payload("a")]})
            + "\n"
            + json.dumps([_proposal_payload("b", artifact_kind="plan", project="prplos-core", fragment_index=1)])
        )
        with self.assertRaisesRegex(llm_output.LlmOutputError, "multiple valid JSON arrays found"):
            llm_output.parse(raw, PROJECTS)

    def test_does_not_unwrap_non_whitelisted_array_key(self):
        raw = json.dumps({"results": [_proposal_payload("a")]})
        with self.assertRaisesRegex(llm_output.LlmOutputError, "no JSON array found"):
            llm_output.parse(raw, PROJECTS)

    def test_does_not_unwrap_object_with_multiple_array_keys(self):
        raw = json.dumps({"findings": [_proposal_payload("a")], "atoms": []})
        with self.assertRaisesRegex(llm_output.LlmOutputError, "no JSON array found"):
            llm_output.parse(raw, PROJECTS)

    def test_does_not_unwrap_wrapper_with_extra_top_level_metadata(self):
        raw = json.dumps({"findings": [], "note": "created file knowledge/foo.md"})
        with self.assertRaisesRegex(llm_output.LlmOutputError, "no JSON array found"):
            llm_output.parse(raw, PROJECTS)


class LlmOutputLenientTests(unittest.TestCase):
    """Lenient repair: drop bad relations / coerce unknown project / skip hard-error
    proposals individually; only fail the whole output when nothing survives."""

    def test_unsupported_relation_type_is_dropped_not_raised(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":["t"],"body":"b",'
            '"source_fragment_indices":[0],"relations":['
            '{"type":"mentations","entity":"MTK"},'
            '{"type":"mentions","entity":"BRCM"}]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(len(proposals), 1)
        # the typo'd edge is dropped; the valid one survives
        self.assertEqual(proposals[0].relations, ({"type": "mentions", "entity": "BRCM"},))

    def test_unknown_project_is_coerced_to_unknown(self):
        raw = (
            '[{"title":"a","artifact_kind":"report","project":"ghost","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].project, "_unknown")

    def test_hard_error_proposal_skipped_others_survive(self):
        raw = (
            '[{"title":"good","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"b1",'
            '"source_fragment_indices":[0],"relations":[]},'
            '{"title":"bad","artifact_kind":"banana","project":"paulshaclaw","tags":[],"body":"b2",'
            '"source_fragment_indices":[1],"relations":[]}]'
        )
        proposals = llm_output.parse(raw, PROJECTS)
        self.assertEqual([p.title for p in proposals], ["good"])

    def test_all_proposals_dropped_raises(self):
        raw = (
            '[{"title":"bad","artifact_kind":"banana","project":"paulshaclaw","tags":[],"body":"b",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        with self.assertRaises(llm_output.LlmOutputError):
            llm_output.parse(raw, PROJECTS)


if __name__ == "__main__":
    unittest.main()
