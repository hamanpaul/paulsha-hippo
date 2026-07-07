import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ImporterCliTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)
        self.memory_root = self.root / "memory"

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def payload(self, session_id="cli-sid-001"):
        return {
            "tool": "copilot-cli",
            "session_id": session_id,
            "capture_scope": "session_end",
            "ended_at": "2026-05-24T13:00:00+00:00",
            "cwd": str(REPO_ROOT),
            "repo": "hamanpaul/paulshaclaw",
            "commit": "e300b08",
            "turn_count": 2,
            "user_prompts": ["add cli", "verify cli"],
            "assistant_summary": "summary",
            "touched_files": ["paulshaclaw/memory/importer/cli.py"],
            "referenced_artifacts": ["docs/superpowers/plans/2026-05-24-stage2-memory-importer-mvp.md"],
        }

    def write_payload(self, name="payload.json", session_id="cli-sid-001"):
        path = self.root / name
        path.write_text(json.dumps(self.payload(session_id), sort_keys=True), encoding="utf-8")
        return path

    def run_cli(self, *args):
        return subprocess.run(
            ["python3", "-m", "paulsha_hippo.importer.cli", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_dry_run_prints_decision_and_rendered_document_without_writing_files(self):
        queue_item = self.write_payload()

        completed = self.run_cli(
            "ingest",
            "--queue-item",
            str(queue_item),
            "--memory-root",
            str(self.memory_root),
            "--dry-run",
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn('"status": "written"', completed.stdout)
        self.assertIn('"dry_run": true', completed.stdout)
        self.assertIn("source_session: cli-sid-001", completed.stdout)
        self.assertFalse((self.memory_root / "inbox").exists())
        self.assertFalse((self.memory_root / "archive").exists())
        self.assertTrue(queue_item.exists())

    def test_normal_ingest_writes_inbox_and_archives_queue_item(self):
        queue_item = self.write_payload(session_id="cli-sid-002")

        completed = self.run_cli(
            "ingest",
            "--queue-item",
            str(queue_item),
            "--memory-root",
            str(self.memory_root),
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn('"status": "written"', completed.stdout)
        decision = json.loads(completed.stdout)
        inbox = self.memory_root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "cli-sid-002.md"
        archive = Path(decision["archive_path"])
        ledger = self.memory_root / "runtime" / "ledger" / "import.jsonl"
        self.assertTrue(inbox.exists())
        self.assertIn("source_session: cli-sid-002", inbox.read_text(encoding="utf-8"))
        self.assertTrue(archive.exists())
        self.assertIn("--written--", archive.name)
        self.assertTrue(ledger.exists())
        self.assertFalse(queue_item.exists())

    def test_missing_queue_item_returns_nonzero_without_traceback(self):
        missing = self.root / "missing.json"

        completed = self.run_cli(
            "ingest",
            "--queue-item",
            str(missing),
            "--memory-root",
            str(self.memory_root),
        )

        self.assertNotEqual(completed.returncode, 0)
        combined = completed.stdout + completed.stderr
        self.assertIn("queue item not found", combined)
        self.assertNotIn("Traceback", combined)

    def test_operational_os_error_returns_nonzero_without_traceback(self):
        queue_item = self.write_payload(session_id="cli-sid-oserror")
        self.memory_root.write_text("not a directory", encoding="utf-8")

        completed = self.run_cli(
            "ingest",
            "--queue-item",
            str(queue_item),
            "--memory-root",
            str(self.memory_root),
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


class Stage2IntegrationCheckScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outside_repo_cwd = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage2_integration_check_succeeds_outside_repo_root(self):
        script = REPO_ROOT / "tests" / "stage2_integration_check.sh"
        repo_local_tmp_parent = REPO_ROOT / ".test-work"
        if repo_local_tmp_parent.exists():
            subprocess.run(["rm", "-rf", str(repo_local_tmp_parent)], check=False)

        completed = subprocess.run(
            ["bash", str(script)],
            cwd=self.outside_repo_cwd,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("[stage2] ok", completed.stdout)
        self.assertFalse(repo_local_tmp_parent.exists())


if __name__ == "__main__":
    unittest.main()
