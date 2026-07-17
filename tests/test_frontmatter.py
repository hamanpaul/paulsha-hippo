import tempfile
import unittest
from pathlib import Path

from paulsha_hippo.importer.adapters import copilot
from paulsha_hippo.importer.frontmatter import render_markdown
from paulsha_hippo.lint.frontmatter_lint import validate_file


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FrontmatterRenderTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def test_render_markdown_uses_stable_frontmatter_key_order_and_lints(self):
        result = copilot.extract(FIXTURES / "copilot/session_end/payload.json")

        rendered = render_markdown(
            result.session,
            project="paulshaclaw",
            classifier_bucket="session",
            captured_at="2026-05-24T12:00:00+00:00",
        )

        expected_frontmatter = [
            "---",
            "memory_layer: inbox",
            "project: paulshaclaw",
            "source_agent: copilot-cli",
            "source_session: copilot-session-end-001",
            "source_artifact: session",
            "title: ",
            "title_source: fallback",
            "captured_at: 2026-05-24T12:00:00+00:00",
            "provenance:",
            "  repo: hamanpaul/paulshaclaw",
            "  commit: e300b08",
            f"  path: {result.session['raw_payload_pointer']}",
            "---",
        ]
        self.assertEqual(rendered.splitlines()[:14], expected_frontmatter)
        doc = self.root / "session.md"
        doc.write_text(rendered, encoding="utf-8")
        self.assertEqual(validate_file(doc), [])

    def test_render_markdown_body_contains_source_cwd_files_artifacts_and_prompts(self):
        result = copilot.extract(FIXTURES / "copilot/with_history/payload.json")

        rendered = render_markdown(
            result.session,
            project="paulshaclaw",
            classifier_bucket="plan",
            captured_at="2026-05-24T12:05:00+00:00",
        )

        self.assertIn("source_artifact: plan", rendered)
        self.assertIn("## Source\n- Tool: copilot-cli\n- Session: copilot-history-001", rendered)
        self.assertIn("## CWD\n/repo/paulshaclaw", rendered)
        self.assertIn("## Touched files\n- tests/test_frontmatter.py\n- docs/plan.md", rendered)
        self.assertIn("## Referenced artifacts\n- docs/plan.md\n- docs/task.md", rendered)
        self.assertIn("## Prompts\n1. Draft frontmatter tests\n2. Keep key order stable", rendered)

    def test_render_markdown_includes_summary_section(self):
        result = copilot.extract(FIXTURES / "copilot/session_end/payload.json")

        rendered = render_markdown(
            result.session,
            project="paulshaclaw",
            classifier_bucket="session",
            captured_at="2026-05-24T12:00:00+00:00",
        )

        self.assertIn("## Summary\nMapped sessionId to session_id.", rendered)

    def test_render_markdown_defaults_empty_body_lists_and_unknown_project(self):
        result = copilot.extract(FIXTURES / "copilot/minimal/payload.json")

        rendered = render_markdown(result.session, captured_at="2026-05-24T12:10:00+00:00")

        self.assertIn("project: _unknown", rendered)
        self.assertIn("source_artifact: session", rendered)
        self.assertIn("  repo: _unknown", rendered)
        self.assertIn("  commit: _unknown", rendered)
        doc = self.root / "minimal.md"
        doc.write_text(rendered, encoding="utf-8")
        self.assertEqual(validate_file(doc), [])
        self.assertIn("## Touched files\n- (none)", rendered)
        self.assertIn("## Referenced artifacts\n- (none)", rendered)
        self.assertIn("## Prompts\n- (none)", rendered)

    def test_render_markdown_uses_unknown_captured_at_when_timestamps_are_missing(self):
        session = {
            "session_id": "synthetic-missing-timestamps",
            "tool": "codex",
            "started_at": None,
            "ended_at": None,
            "cwd": None,
            "repo": None,
            "commit": None,
            "turn_count": 1,
            "user_prompts": [],
            "assistant_summary": "",
            "touched_files": [],
            "referenced_artifacts": [],
            "raw_payload_pointer": "queue/synthetic.json",
        }

        rendered = render_markdown(session)

        self.assertIn("captured_at: _unknown", rendered)
        doc = self.root / "missing-timestamps.md"
        doc.write_text(rendered, encoding="utf-8")
        self.assertEqual(validate_file(doc), [])

    def test_render_markdown_quotes_frontmatter_scalars_with_yaml_special_characters(self):
        session = {
            "session_id": "session: 001 # needs quoting",
            "tool": "codex \"quoted\"",
            "started_at": "2026-05-24T12:15:00+00:00",
            "ended_at": None,
            "cwd": None,
            "repo": "hamanpaul/paulshaclaw: main # branch",
            "commit": "abc\"def",
            "turn_count": 1,
            "user_prompts": [],
            "assistant_summary": "",
            "touched_files": [],
            "referenced_artifacts": [],
            "raw_payload_pointer": "queue/path\nwith: colon # hash",
        }

        rendered = render_markdown(
            session,
            project="paul: shaclaw # lab",
            classifier_bucket="report: draft # tagged",
            memory_layer="inbox # triage",
        )

        expected_lines = [
            'memory_layer: "inbox # triage"',
            'project: "paul: shaclaw # lab"',
            'source_agent: "codex \\"quoted\\""',
            'source_session: "session: 001 # needs quoting"',
            'source_artifact: "report: draft # tagged"',
            '  repo: "hamanpaul/paulshaclaw: main # branch"',
            '  commit: "abc\\"def"',
            '  path: "queue/path\\nwith: colon # hash"',
        ]
        for line in expected_lines:
            with self.subTest(line=line):
                self.assertIn(line, rendered)
        self.assertNotIn("queue/path\nwith: colon", rendered)
        doc = self.root / "quoted-scalars.md"
        doc.write_text(rendered, encoding="utf-8")
        self.assertEqual(validate_file(doc), [])


if __name__ == "__main__":
    unittest.main()
