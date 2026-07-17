from __future__ import annotations

import unittest

from paulsha_hippo.atomizer import prompt as prompt_mod
from paulsha_hippo.atomizer.splitter import Fragment


def _frag(index, body):
    return Fragment(
        project="paulshaclaw",
        source_agent="claude",
        source_session="s1",
        source_artifact="research",
        captured_at="2026-06-02T00:00:00Z",
        provenance={"repo": "r", "commit": "c", "path": "p"},
        fragment_index=index,
        body=body,
    )


def _expected_output_footer() -> list[str]:
    return [
        "## Output",
        'Return ONLY this canonical JSON object shape: {"schema_version":1,"disposition":"findings|no_findings","reason":null|string,"findings":[...]}',
        "Use disposition=findings with one or more findings and reason=null.",
        "Use disposition=no_findings only with findings=[] and a non-empty reason.",
        "The first character must be `{` and the last character must be `}`.",
        "Do NOT create files, write files, save files, or claim that you updated any file or index.",
        "Do NOT return prose, narration, summaries, markdown fences, or any text before or after the JSON object.",
    ]


class PromptSessionHintTests(unittest.TestCase):
    def test_includes_session_project_hint_when_known(self):
        frag = Fragment(
            project="PROJECT-0602", source_agent="claude", source_session="s1",
            source_artifact="research", captured_at="2026-06-02T00:00:00Z",
            provenance={}, fragment_index=0, body="mtk pon work",
        )
        text = prompt_mod.build_prompt("SKILL", [frag], ["paulshaclaw", "PROJECT-0602"])
        self.assertIn("This session was captured in project: PROJECT-0602", text)
        self.assertIn("Use this exact project for every finding", text)

    def test_omits_hint_when_session_project_not_in_known(self):
        frag = Fragment(
            project="_unknown", source_agent="claude", source_session="s1",
            source_artifact="research", captured_at="2026-06-02T00:00:00Z",
            provenance={}, fragment_index=0, body="x",
        )
        text = prompt_mod.build_prompt("SKILL", [frag], ["paulshaclaw", "PROJECT-0602"])
        self.assertNotIn("This session was captured in project", text)


class PromptTests(unittest.TestCase):
    def test_matches_plan_prompt_sections(self):
        text = prompt_mod.build_prompt(
            "SKILLDOC",
            [_frag(0, "alpha"), _frag(1, "beta")],
            ["paulshaclaw", "prplos-core"],
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "SKILLDOC",
                    "",
                    "## Known projects (choose exactly one per slice, or _unknown)",
                    "paulshaclaw, prplos-core",
                    "",
                    "## This session's project",
                    "This session was captured in project: paulshaclaw. "
                    "Use this exact project for every finding; do not re-home the source session.",
                    "",
                    "## Session fragments to atomize",
                    "[fragment 0]",
                    "alpha",
                    "",
                    "[fragment 1]",
                    "beta",
                    "",
                    *_expected_output_footer(),
                ]
            ),
        )

    def test_preserves_skill_text_verbatim(self):
        text = prompt_mod.build_prompt(
            "SKILLDOC\n",
            [_frag(0, "alpha")],
            ["paulshaclaw"],
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "SKILLDOC\n",
                    "",
                    "## Known projects (choose exactly one per slice, or _unknown)",
                    "paulshaclaw",
                    "",
                    "## This session's project",
                    "This session was captured in project: paulshaclaw. "
                    "Use this exact project for every finding; do not re-home the source session.",
                    "",
                    "## Session fragments to atomize",
                    "[fragment 0]",
                    "alpha",
                    "",
                    *_expected_output_footer(),
                ]
            ),
        )

    def test_runtime_footer_repeats_hardened_output_contract(self):
        text = prompt_mod.build_prompt("SKILLDOC", [_frag(0, "alpha")], ["paulshaclaw"])
        footer = "\n".join(_expected_output_footer())
        self.assertIn(footer, text)

    def test_deterministic(self):
        frags = [_frag(0, "alpha")]
        self.assertEqual(
            prompt_mod.build_prompt("S", frags, ["p"]),
            prompt_mod.build_prompt("S", frags, ["p"]),
        )


if __name__ == "__main__":
    unittest.main()
