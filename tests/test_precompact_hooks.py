"""Tests for precompact hooks (Task 5)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / "paulsha_hippo" / "hooks"


def _run_hook(script_name, stdin_data, extra_env=None):
    env = {**os.environ, "PSC_MEMORY_ROOT": ""}
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", str(HOOKS_DIR / script_name)]
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        input=stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data),
        env=env,
        capture_output=True,
        text=True,
    )


class PreCompactHooksTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.memory_root = Path(self.tmp.name) / "memory"
        # Create required directories
        (self.memory_root / "runtime" / "queue").mkdir(parents=True)
        (self.memory_root / "log").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def _env(self, extra=None):
        e = {"PSC_MEMORY_ROOT": str(self.memory_root)}
        if extra:
            e.update(extra)
        return e

    # ------------------------------------------------------------------
    # Claude precompact hook
    # ------------------------------------------------------------------

    def test_claude_precompact_exits_zero_on_empty_stdin(self):
        result = _run_hook("claude_precompact.py", "", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_claude_precompact_exits_zero_on_invalid_json(self):
        result = _run_hook("claude_precompact.py", "bad{{json", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_claude_precompact_writes_queue_payload_with_pre_compact_scope(self):
        """Claude precompact hook writes capture_scope=pre_compact."""
        payload = {"session_id": "claude-compact-001", "cwd": "/repo"}

        result = _run_hook("claude_precompact.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self.memory_root / "runtime" / "queue" / "claude-code__claude-compact-001.json"
        self.assertTrue(queue_file.exists(), f"queue file not found: {queue_file}")
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "pre_compact")
        self.assertEqual(written["tool"], "claude-code")
        self.assertEqual(written["session_id"], "claude-compact-001")

    def test_claude_precompact_queue_file_written_atomically(self):
        """No .tmp file left behind after successful write."""
        payload = {"session_id": "claude-atomic-compact-001"}
        _run_hook("claude_precompact.py", payload, extra_env=self._env())

        queue_dir = self.memory_root / "runtime" / "queue"
        tmp_files = list(queue_dir.glob(".*.tmp"))
        self.assertEqual(tmp_files, [], f"Leftover .tmp files: {tmp_files}")

    def test_claude_precompact_logs_warn_on_invalid_json(self):
        result = _run_hook("claude_precompact.py", "bad json", extra_env=self._env())
        self.assertEqual(result.returncode, 0)
        hooks_log = self.memory_root / "log" / "hooks.log"
        self.assertTrue(hooks_log.exists(), "hooks.log should be written on parse error")
        self.assertIn("WARN", hooks_log.read_text())

    # ------------------------------------------------------------------
    # Copilot precompact hook
    # ------------------------------------------------------------------

    def test_copilot_precompact_exits_zero_on_empty_stdin(self):
        result = _run_hook("copilot_precompact.py", "", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_copilot_precompact_exits_zero_on_invalid_json(self):
        result = _run_hook("copilot_precompact.py", "not-json", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_copilot_precompact_writes_queue_payload_with_pre_compact_scope(self):
        """Copilot precompact hook writes capture_scope=pre_compact."""
        payload = {"sessionId": "copilot-compact-001", "cwd": "/repo"}

        result = _run_hook("copilot_precompact.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = (
            self.memory_root / "runtime" / "queue" / "copilot-cli__copilot-compact-001.json"
        )
        self.assertTrue(queue_file.exists(), f"queue file not found: {queue_file}")
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "pre_compact")
        self.assertEqual(written["tool"], "copilot-cli")
        self.assertEqual(written["session_id"], "copilot-compact-001")

    def test_copilot_precompact_normalizes_camel_case_session_id(self):
        """Copilot precompact normalizes sessionId to session_id."""
        payload = {"sessionId": "copilot-compact-002"}

        result = _run_hook("copilot_precompact.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = (
            self.memory_root / "runtime" / "queue" / "copilot-cli__copilot-compact-002.json"
        )
        self.assertTrue(queue_file.exists())
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["session_id"], "copilot-compact-002")
        self.assertNotIn("sessionId", written)

    def test_copilot_precompact_queue_file_written_atomically(self):
        """No .tmp file left behind after successful write."""
        payload = {"sessionId": "copilot-atomic-compact-001"}
        _run_hook("copilot_precompact.py", payload, extra_env=self._env())

        queue_dir = self.memory_root / "runtime" / "queue"
        tmp_files = list(queue_dir.glob(".*.tmp"))
        self.assertEqual(tmp_files, [], f"Leftover .tmp files: {tmp_files}")


if __name__ == "__main__":
    unittest.main()
