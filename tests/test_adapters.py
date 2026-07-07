import json
import tempfile
import unittest
from pathlib import Path

from paulsha_hippo.importer.adapters import claude, codex, copilot
from paulsha_hippo.importer.adapters.base import read_payload


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NORMALIZED_KEYS = {
    "session_id",
    "tool",
    "started_at",
    "ended_at",
    "cwd",
    "repo",
    "commit",
    "turn_count",
    "user_prompts",
    "assistant_summary",
    "touched_files",
    "referenced_artifacts",
    "raw_payload_pointer",
    "title_source",
}
FRONTMATTER_ONLY_KEYS = {
    "source_agent",
    "source_session",
    "source_repo",
    "source_commit",
    "source_artifact",
}


class AdapterContractTest(unittest.TestCase):
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

    def load(self, relative):
        path = FIXTURES / relative / "payload.json"
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return path, payload

    def assert_normalized_shape(self, session):
        self.assertEqual(set(session), NORMALIZED_KEYS)
        self.assertTrue(FRONTMATTER_ONLY_KEYS.isdisjoint(session))
        self.assertIsInstance(session["session_id"], str)
        self.assertIn(session["tool"], {"claude-code", "codex", "copilot-cli"})
        self.assertGreaterEqual(session["turn_count"], 1)
        for key in ["user_prompts", "touched_files", "referenced_artifacts"]:
            self.assertIsInstance(session[key], list)
        self.assertIsInstance(session["assistant_summary"], str)
        self.assertIsInstance(session["raw_payload_pointer"], str)

    def assert_queue_metadata(self, result, expected_scope, expected_payload):
        self.assertEqual(result.capture_scope, expected_scope)
        self.assertEqual(result.raw_payload, expected_payload)
        self.assertNotIn("capture_scope", result.session)
        self.assertNotIn("raw_payload", result.session)

    def test_claude_session_end_fixture_normalizes_contract_and_queue_metadata(self):
        path, payload = self.load("claude/session_end")

        result = claude.extract(path)

        self.assert_normalized_shape(result.session)
        self.assert_queue_metadata(result, "session_end", payload)
        self.assertEqual(result.session["session_id"], "claude-session-end-001")
        self.assertEqual(result.session["tool"], "claude-code")
        self.assertEqual(result.session["turn_count"], 4)
        self.assertEqual(result.session["ended_at"], "2026-05-24T08:45:00+00:00")
        self.assertEqual(result.session["referenced_artifacts"], ["docs/superpowers/plans/2026-05-24-stage2-memory-importer-mvp.md"])
        self.assertEqual(result.session["raw_payload_pointer"], str(path))


    def test_claude_with_artifacts_fixture_preserves_repo_commit_and_artifacts(self):
        path, payload = self.load("claude/with_artifacts")

        result = claude.extract(path)

        self.assert_normalized_shape(result.session)
        self.assert_queue_metadata(result, "session_end", payload)
        self.assertEqual(result.session["session_id"], "claude-artifacts-001")
        self.assertEqual(result.session["repo"], "hamanpaul/paulshaclaw")
        self.assertEqual(result.session["commit"], "4f733f4")
        self.assertEqual(result.session["touched_files"], ["openspec/changes/stage2-memory-importer-mvp/tasks.md"])
        self.assertEqual(result.session["referenced_artifacts"], ["docs/spec.md", "docs/research/02.obs-auto-moc-memory-dream-mode-24-7-service-notes-.md"])

    def test_codex_stop_is_turn_scoped_and_not_session_ended(self):
        path, payload = self.load("codex/stop")

        result = codex.extract(path)

        self.assert_normalized_shape(result.session)
        self.assert_queue_metadata(result, "turn", payload)
        self.assertEqual(result.session["session_id"], "codex-stop-001")
        self.assertEqual(result.session["tool"], "codex")
        self.assertIsNone(result.session["ended_at"])
        self.assertEqual(result.session["touched_files"], ["tests/test_adapters.py"])

    def test_codex_subagent_stop_keeps_subagent_capture_scope_outside_session(self):
        path, payload = self.load("codex/subagent_stop")

        result = codex.extract(path)

        self.assert_normalized_shape(result.session)
        self.assert_queue_metadata(result, "subagent", payload)
        self.assertEqual(result.session["session_id"], "codex-subagent-001")
        self.assertEqual(result.session["turn_count"], 1)

    def test_copilot_session_id_camel_case_becomes_normalized_session_id(self):
        path, payload = self.load("copilot/session_end")

        result = copilot.extract(path)

        self.assert_normalized_shape(result.session)
        self.assert_queue_metadata(result, "session_end", payload)
        self.assertEqual(result.session["session_id"], payload["sessionId"])
        self.assertNotIn("sessionId", result.session)
        self.assertEqual(result.session["tool"], "copilot-cli")
        self.assertEqual(result.session["ended_at"], payload["timestamp"])

    def test_copilot_history_fixture_derives_prompts_and_turn_count(self):
        path, payload = self.load("copilot/with_history")

        result = copilot.extract(path)

        self.assert_normalized_shape(result.session)
        self.assertEqual(result.session["session_id"], "copilot-history-001")
        self.assertEqual(result.session["turn_count"], 3)
        self.assertEqual(result.session["user_prompts"], ["Draft frontmatter tests", "Keep key order stable"])
        self.assertEqual(result.session["referenced_artifacts"], ["docs/plan.md", "docs/task.md"])

    def test_missing_fields_use_spec_defaults_without_raising(self):
        cases = [
            (claude, "claude/minimal", "claude-code", "session_end"),
            (codex, "codex/minimal", "codex", "turn"),
            (copilot, "copilot/minimal", "copilot-cli", "session_end"),
        ]
        for module, fixture, tool, scope in cases:
            with self.subTest(fixture=fixture):
                path, payload = self.load(fixture)

                result = module.extract(path)

                self.assert_normalized_shape(result.session)
                self.assert_queue_metadata(result, scope, payload)
                self.assertEqual(result.session["tool"], tool)
                self.assertGreaterEqual(result.session["turn_count"], 1)
                self.assertEqual(result.session["user_prompts"], [])
                self.assertEqual(result.session["assistant_summary"], "")
                self.assertEqual(result.session["touched_files"], [])
                self.assertEqual(result.session["referenced_artifacts"], [])
                self.assertIsNone(result.session["repo"])
                self.assertIsNone(result.session["commit"])

    def test_read_payload_rejects_top_level_non_object_json(self):
        cases = [
            ("array", ["not", "an", "object"]),
            ("string", "not an object"),
        ]
        for name, payload in cases:
            with self.subTest(name=name):
                path = self.root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "top-level JSON object"):
                    read_payload(path)


if __name__ == "__main__":
    unittest.main()
