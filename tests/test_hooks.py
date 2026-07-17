"""Tests for hook queue writers, installer, and uninstaller (Task 5)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / "paulsha_hippo" / "hooks"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _run_hook(script_name, stdin_data, extra_env=None, extra_args=None):
    env = {**os.environ, "PSC_MEMORY_ROOT": ""}  # caller overrides
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", str(HOOKS_DIR / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        input=stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data),
        env=env,
        capture_output=True,
        text=True,
    )


def _run_install(args, extra_env=None):
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "install.sh")] + args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_uninstall(args, extra_env=None):
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "uninstall.sh")] + args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


class HookQueueWriterTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.memory_root = Path(self.tmp.name) / "memory"
        # Pre-create required dirs so hooks can write
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

    def _queue_capture(self, tool: str, session_id: str) -> Path:
        matches = sorted(
            (self.memory_root / "runtime" / "queue").glob(f"{tool}__{session_id}__*.json")
        )
        self.assertEqual(len(matches), 1, f"expected one queue capture, got {matches}")
        return matches[0]

    # ------------------------------------------------------------------
    # Claude hook
    # ------------------------------------------------------------------

    def test_claude_hook_writes_queue_payload_with_session_end_scope(self):
        fixture = FIXTURES / "claude" / "session_end" / "payload.json"
        payload = json.loads(fixture.read_text())

        result = _run_hook("claude_session_end.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self._queue_capture("claude-code", "claude-session-end-001")
        self.assertTrue(queue_file.exists(), f"queue file not found: {queue_file}")
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "session_end")
        self.assertEqual(written["tool"], "claude-code")
        self.assertEqual(written["session_id"], "claude-session-end-001")
        self.assertEqual(written["capture_id"], queue_file.stem.rsplit("__", 1)[-1])

    def test_claude_hook_always_sets_capture_scope_session_end(self):
        payload = {"session_id": "claude-plain-001", "cwd": "/repo"}

        result = _run_hook("claude_session_end.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self._queue_capture("claude-code", "claude-plain-001")
        self.assertTrue(queue_file.exists())
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "session_end")
        self.assertTrue(written["capture_id"])

    def test_claude_hook_exits_zero_on_empty_stdin(self):
        result = _run_hook("claude_session_end.py", "", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_claude_hook_exits_zero_on_invalid_json_stdin(self):
        result = _run_hook("claude_session_end.py", "not-json{{{", extra_env=self._env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_claude_hook_logs_warn_on_invalid_json(self):
        result = _run_hook("claude_session_end.py", "bad json", extra_env=self._env())
        self.assertEqual(result.returncode, 0)
        hooks_log = self.memory_root / "log" / "hooks.log"
        self.assertTrue(hooks_log.exists(), "hooks.log should be written on parse error")
        self.assertIn("WARN", hooks_log.read_text())

    def test_claude_hook_queue_file_written_atomically(self):
        """No .tmp file left behind after successful write."""
        payload = {"session_id": "claude-atomic-001"}
        _run_hook("claude_session_end.py", payload, extra_env=self._env())

        queue_dir = self.memory_root / "runtime" / "queue"
        tmp_files = list(queue_dir.glob(".*.tmp"))
        self.assertEqual(tmp_files, [], f"Leftover .tmp files: {tmp_files}")

    # ------------------------------------------------------------------
    # Codex hook
    # ------------------------------------------------------------------

    def test_codex_hook_without_subagent_flag_uses_turn_scope(self):
        fixture = FIXTURES / "codex" / "stop" / "payload.json"
        payload = json.loads(fixture.read_text())

        result = _run_hook("codex_session_end.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        matches = sorted((self.memory_root / "runtime" / "queue").glob("codex__codex-stop-001*.json"))
        self.assertEqual(len(matches), 1, f"expected one queue file, got {matches}")
        queue_file = matches[0]
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "turn")
        self.assertEqual(written["tool"], "codex")
        self.assertIsNone(written.get("ended_at"))

    def test_codex_hook_with_subagent_flag_uses_subagent_scope(self):
        fixture = FIXTURES / "codex" / "subagent_stop" / "payload.json"
        payload = json.loads(fixture.read_text())

        result = _run_hook(
            "codex_session_end.py", payload,
            extra_env=self._env(), extra_args=["--subagent"]
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        matches = sorted((self.memory_root / "runtime" / "queue").glob("codex__codex-subagent-001*.json"))
        self.assertEqual(len(matches), 1, f"expected one queue file, got {matches}")
        queue_file = matches[0]
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["capture_scope"], "subagent")
        self.assertIsNone(written.get("ended_at"))

    def test_codex_hook_without_subagent_flag_does_not_set_ended_at(self):
        """Codex Stop events are mid-session; ended_at must remain absent."""
        payload = {"session_id": "codex-turn-007", "cwd": "/repo"}

        result = _run_hook("codex_session_end.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        matches = sorted((self.memory_root / "runtime" / "queue").glob("codex__codex-turn-007*.json"))
        self.assertEqual(len(matches), 1, f"expected one queue file, got {matches}")
        queue_file = matches[0]
        written = json.loads(queue_file.read_text())
        self.assertIn("ended_at", written)
        self.assertIsNone(written["ended_at"])

    def test_codex_hook_exits_zero_on_invalid_json(self):
        result = _run_hook("codex_session_end.py", "{{bad", extra_env=self._env())
        self.assertEqual(result.returncode, 0)

    def test_codex_hook_writes_unique_queue_file_per_event_for_same_session(self):
        payload = {"session_id": "codex-race-001", "cwd": "/repo"}

        first = _run_hook("codex_session_end.py", payload, extra_env=self._env())
        second = _run_hook("codex_session_end.py", payload, extra_env=self._env())

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        matches = sorted((self.memory_root / "runtime" / "queue").glob("codex__codex-race-001*.json"))
        self.assertEqual(len(matches), 2, f"expected distinct queue files, got {matches}")
        for queue_file in matches:
            written = json.loads(queue_file.read_text())
            self.assertEqual(written["session_id"], "codex-race-001")

    # ------------------------------------------------------------------
    # Copilot hook
    # ------------------------------------------------------------------

    def test_copilot_hook_normalizes_camel_case_session_id(self):
        fixture = FIXTURES / "copilot" / "session_end" / "payload.json"
        payload = json.loads(fixture.read_text())

        result = _run_hook("copilot_session_end.py", payload, extra_env=self._env())

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self._queue_capture("copilot-cli", "copilot-session-end-001")
        self.assertTrue(queue_file.exists(), f"expected {queue_file}")
        written = json.loads(queue_file.read_text())
        self.assertEqual(written["session_id"], "copilot-session-end-001")
        self.assertEqual(written["tool"], "copilot-cli")
        self.assertEqual(written["capture_scope"], "session_end")
        self.assertTrue(written["capture_id"])

    def test_copilot_hook_supplements_missing_fields_from_history_file(self):
        """If history file exists, copilot hook reads it to supplement missing fields."""
        sid = "copilot-hist-supp-001"
        # Minimal stdin — no turns/prompts
        stdin_payload = {"sessionId": sid, "reason": "sessionEnd"}

        # Write a history file to the fake config root
        config_root = Path(self.tmp.name) / "config_root"
        history_dir = config_root / ".copilot" / "history-session-state"
        history_dir.mkdir(parents=True)
        history_file = history_dir / f"session_{sid}_001.json"
        history_content = {
            "sessionId": sid,
            "cwd": "/repo/supplemented",
            "history": [
                {"role": "user", "content": "supplemented prompt"},
                {"role": "assistant", "content": "supplemented reply"},
            ],
            "touched_files": ["src/supplemented.py"],
        }
        history_file.write_text(json.dumps(history_content), encoding="utf-8")

        result = _run_hook(
            "copilot_session_end.py", stdin_payload,
            extra_env={**self._env(), "PSC_CONFIG_ROOT": str(config_root)},
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self._queue_capture("copilot-cli", sid)
        self.assertTrue(queue_file.exists(), f"expected {queue_file}")
        written = json.loads(queue_file.read_text())
        # Supplemented fields should appear
        self.assertEqual(written.get("cwd"), "/repo/supplemented")
        self.assertIn("supplemented prompt", str(written.get("history", [])))

    def test_copilot_hook_succeeds_when_no_history_file(self):
        """copilot hook must not fail if history-session-state files are absent."""
        config_root = Path(self.tmp.name) / "config_root_empty"
        stdin_payload = {"sessionId": "copilot-nohist-001", "reason": "sessionEnd"}

        result = _run_hook(
            "copilot_session_end.py", stdin_payload,
            extra_env={**self._env(), "PSC_CONFIG_ROOT": str(config_root)},
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        queue_file = self._queue_capture("copilot-cli", "copilot-nohist-001")
        self.assertTrue(queue_file.exists())

    def test_copilot_hook_exits_zero_on_invalid_json(self):
        result = _run_hook("copilot_session_end.py", "not-json", extra_env=self._env())
        self.assertEqual(result.returncode, 0)

    # ------------------------------------------------------------------
    # Generic: importer unavailability does not cause hook failure
    # ------------------------------------------------------------------

    def test_hook_exits_zero_when_venv_python_is_absent(self):
        """Hook exits 0 even if .venv/bin/python is not present (best-effort)."""
        payload = {"session_id": "claude-novenv-001"}
        # memory_root has no .venv
        result = _run_hook("claude_session_end.py", payload, extra_env=self._env())
        self.assertEqual(result.returncode, 0)
        # Queue file still written
        queue_file = self._queue_capture("claude-code", "claude-novenv-001")
        self.assertTrue(queue_file.exists())


# ---------------------------------------------------------------------------
# Installer / Uninstaller tests
# ---------------------------------------------------------------------------

class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)
        self.memory_root = self.root / "memory"
        self.config_root = self.root / "config_root"
        self.repo_root = str(REPO_ROOT)
        # Common install args (skip venv to keep tests fast)
        self.base_args = [
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
            "--repo-root", self.repo_root,
            "--skip-venv",
        ]

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Full install — memory tree + hook scripts
    # ------------------------------------------------------------------

    def test_full_install_creates_memory_tree(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((self.memory_root / "inbox" / "sessions").is_dir())
        self.assertTrue((self.memory_root / "runtime" / "queue").is_dir())

    def test_full_install_rejects_memory_root_with_whitespace(self):
        memory_root = self.root / "memory root"

        result = _run_install(
            [
                "--memory-root", str(memory_root),
                "--config-root", str(self.config_root),
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain whitespace", result.stderr)

    def test_full_install_rejects_config_root_with_whitespace(self):
        config_root = self.root / "config root"

        result = _run_install(
            [
                "--memory-root", str(self.memory_root),
                "--config-root", str(config_root),
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain whitespace", result.stderr)

    def test_full_install_rejects_empty_config_root(self):
        result = _run_install(
            [
                "--memory-root", str(self.memory_root),
                "--config-root", "",
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config-root must not be empty", result.stderr)

    def test_full_install_rejects_filesystem_root_config_root(self):
        result = _run_install(
            [
                "--memory-root", str(self.memory_root),
                "--config-root", "/",
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config-root must not be /", result.stderr)

    def test_full_install_deploys_hook_scripts(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for script in (
            "install.sh",
            "uninstall.sh",
            "claude_session_end.py",
            "codex_session_end.py",
            "copilot_session_end.py",
            # Session-start and precompact related scripts
            "_wakeup_common.py",
            "_bootstrap.py",
            "claude_session_start.py",
            "codex_session_start.py",
            "copilot_session_start.py",
            "claude_precompact.py",
            "copilot_precompact.py",
            # Consumption-loop hooks
            "claude_user_prompt_submit.py",
            "claude_post_tool_use.py",
            "_shortlist_common.py",
            "copilot_user_prompt_submit.py",
            "copilot_post_tool_use.py",
        ):
            deployed = self.memory_root / "hooks" / script
            self.assertTrue(deployed.exists(), f"{script} not deployed")

    # ------------------------------------------------------------------
    # Full install — Claude config
    # ------------------------------------------------------------------

    def test_full_install_writes_claude_settings_with_hook_entry(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        settings_path = self.config_root / ".claude" / "settings.json"
        self.assertTrue(settings_path.exists(), "claude settings.json not created")
        settings = json.loads(settings_path.read_text())
        session_end_hooks = settings.get("hooks", {}).get("SessionEnd", [])
        self.assertTrue(len(session_end_hooks) > 0, "No SessionEnd hook entries")
        commands = [
            h["command"]
            for entry in session_end_hooks
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertTrue(
            any("claude_session_end.py" in cmd for cmd in commands),
            f"claude_session_end.py not found in hook commands: {commands}",
        )
        self.assertTrue(
            any(f"PSC_MEMORY_ROOT={self.memory_root}" in cmd for cmd in commands),
            f"PSC_MEMORY_ROOT override missing from Claude hook commands: {commands}",
        )
        self.assertTrue(
            any(f"PSC_CONFIG_ROOT={self.config_root}" in cmd for cmd in commands),
            f"PSC_CONFIG_ROOT override missing from Claude hook commands: {commands}",
        )

    def test_full_install_writes_claude_sessionstart_and_precompact_entries(self):
        """Assert SessionStart and PreCompact managed entries are present for Claude."""
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        settings_path = self.config_root / ".claude" / "settings.json"
        self.assertTrue(settings_path.exists(), "claude settings.json not created")
        settings = json.loads(settings_path.read_text())
        session_start_hooks = settings.get("hooks", {}).get("SessionStart", [])
        precompact_hooks = settings.get("hooks", {}).get("PreCompact", [])
        self.assertTrue(len(session_start_hooks) > 0, "No SessionStart hook entries")
        self.assertTrue(len(precompact_hooks) > 0, "No PreCompact hook entries")

        # Extract commands
        start_commands = [h["command"] for entry in session_start_hooks for h in entry.get("hooks", []) if "command" in h]
        pre_commands = [h["command"] for entry in precompact_hooks for h in entry.get("hooks", []) if "command" in h]

        self.assertTrue(any("claude_session_start.py" in c for c in start_commands), f"claude_session_start.py not found: {start_commands}")
        self.assertTrue(any("claude_precompact.py" in c for c in pre_commands), f"claude_precompact.py not found: {pre_commands}")
        self.assertTrue(any(f"PSC_MEMORY_ROOT={self.memory_root}" in c for c in start_commands), f"PSC_MEMORY_ROOT missing in SessionStart: {start_commands}")
        self.assertTrue(any(f"PSC_CONFIG_ROOT={self.config_root}" in c for c in start_commands), f"PSC_CONFIG_ROOT missing in SessionStart: {start_commands}")

    def test_full_install_wires_consumption_loop_hooks(self):
        """UserPromptSubmit + PostToolUse(matcher=Read) managed entries are registered,
        coexist with a pre-existing UserPromptSubmit hook, and are idempotent."""
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        # seed a pre-existing (non-managed) UserPromptSubmit hook, as in production (codegraph)
        settings_path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "codegraph prompt-hook"}]}]}}), encoding="utf-8")

        self.assertEqual(_run_install(self.base_args).returncode, 0)
        self.assertEqual(_run_install(self.base_args).returncode, 0)  # idempotent re-run
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})

        ups = [h for e in hooks.get("UserPromptSubmit", []) for h in e.get("hooks", [])]
        ups_cmds = [h.get("command", "") for h in ups]
        self.assertTrue(any(c == "codegraph prompt-hook" for c in ups_cmds), "codegraph hook lost")
        self.assertEqual(
            sum("claude_user_prompt_submit.py" in c for c in ups_cmds), 1,
            f"UserPromptSubmit memory hook not exactly-once: {ups_cmds}")

        ptu_entries = hooks.get("PostToolUse", [])
        read_mem = [
            h for e in ptu_entries if e.get("matcher") == "Read"
            for h in e.get("hooks", []) if "claude_post_tool_use.py" in h.get("command", "")
        ]
        self.assertEqual(len(read_mem), 1, f"PostToolUse(Read) memory hook not exactly-once: {ptu_entries}")
        self.assertEqual(read_mem[0].get("managedBy"), "paulsha-memory")

    def test_full_install_writes_copilot_sessionstart_and_precompact_arrays(self):
        """Assert Copilot config contains sessionStart and preCompact arrays."""
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hook_path = self.config_root / ".copilot" / "hooks" / "paulsha-memory.json"
        self.assertTrue(hook_path.exists(), "copilot paulsha-memory.json not created")
        config = json.loads(hook_path.read_text())

        self.assertIn("sessionStart", config.get("hooks", {}))
        self.assertIn("preCompact", config.get("hooks", {}))

        session_start = config.get("hooks", {}).get("sessionStart", [])
        precompact = config.get("hooks", {}).get("preCompact", [])

        bash_cmds_start = [e.get("bash", "") for e in session_start]
        bash_cmds_pre = [e.get("bash", "") for e in precompact]

        self.assertTrue(any("copilot_session_start.py" in c for c in bash_cmds_start), f"copilot_session_start.py not in sessionStart: {bash_cmds_start}")
        self.assertTrue(any("copilot_precompact.py" in c for c in bash_cmds_pre), f"copilot_precompact.py not in preCompact: {bash_cmds_pre}")

    def test_full_install_wires_copilot_consumption_loop_hooks(self):
        """copilot config 用官方事件 key：userPromptSubmitted（prompt-time shortlist）
        與 postToolUse（view read attribution）——capability matrix 2026-07-11 復測 supported。"""
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hook_path = self.config_root / ".copilot" / "hooks" / "paulsha-memory.json"
        config = json.loads(hook_path.read_text())
        hooks = config.get("hooks", {})

        # 官方 key 是 userPromptSubmitted（非 userPromptSubmit——該 key 不存在，hook 不會 fire）
        self.assertNotIn("userPromptSubmit", hooks)
        ups = hooks.get("userPromptSubmitted", [])
        ups_cmds = [e.get("bash", "") for e in ups]
        self.assertTrue(
            any("copilot_user_prompt_submit.py" in c for c in ups_cmds),
            f"copilot_user_prompt_submit.py not in userPromptSubmitted: {ups_cmds}",
        )

        ptu = hooks.get("postToolUse", [])
        ptu_cmds = [e.get("bash", "") for e in ptu]
        self.assertTrue(
            any("copilot_post_tool_use.py" in c for c in ptu_cmds),
            f"copilot_post_tool_use.py not in postToolUse: {ptu_cmds}",
        )

    def test_full_install_preserves_existing_claude_settings_non_hook_keys(self):
        """Should not clobber existing keys outside 'hooks' in settings.json."""
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"existing_key": "preserved_value", "hooks": {}}),
            encoding="utf-8",
        )

        _run_install(self.base_args)

        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings.get("existing_key"), "preserved_value")

    def test_full_install_preserves_unrelated_claude_hook_with_matching_substring(self):
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_command = "/tmp/custom-wrapper --delegate claude_session_end.py --keep"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionEnd": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": unrelated_command},
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_install(self.base_args)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        settings = json.loads(settings_path.read_text())
        commands = [
            h["command"]
            for entry in settings.get("hooks", {}).get("SessionEnd", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertIn(unrelated_command, commands)
        self.assertEqual(sum("claude_session_end.py" in command for command in commands), 2)

    def test_full_install_fails_without_overwriting_invalid_claude_settings(self):
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        original = '{"hooks": invalid-json}'
        settings_path.write_text(original, encoding="utf-8")

        result = _run_install(self.base_args)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(settings_path.read_text(encoding="utf-8"), original)

    def test_full_install_does_not_duplicate_claude_hook_entry(self):
        """Running install twice should not add duplicate hook entries."""
        _run_install(self.base_args)
        _run_install(self.base_args)

        settings_path = self.config_root / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        session_end_hooks = settings.get("hooks", {}).get("SessionEnd", [])
        commands = [
            h["command"]
            for entry in session_end_hooks
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        matching = [c for c in commands if "claude_session_end.py" in c]
        self.assertEqual(len(matching), 1, f"Expected 1 claude hook entry, got: {matching}")

    def test_reinstall_updates_claude_hook_command_to_new_memory_root(self):
        first_memory_root = self.root / "memory-first"
        second_memory_root = self.root / "memory-second"
        first_args = [
            "--memory-root", str(first_memory_root),
            "--config-root", str(self.config_root),
            "--repo-root", self.repo_root,
            "--skip-venv",
        ]
        second_args = [
            "--memory-root", str(second_memory_root),
            "--config-root", str(self.config_root),
            "--repo-root", self.repo_root,
            "--skip-venv",
        ]

        self.assertEqual(_run_install(first_args).returncode, 0)
        self.assertEqual(_run_install(second_args).returncode, 0)

        settings_path = self.config_root / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        commands = [
            h["command"]
            for entry in settings.get("hooks", {}).get("SessionEnd", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(
            [cmd for cmd in commands if "claude_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={second_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{second_memory_root}/hooks/.venv/bin/python {second_memory_root}/hooks/claude_session_end.py"
            ],
        )

    def test_reinstall_replaces_legacy_prefixed_claude_hook_without_managed_marker(self):
        old_memory_root = self.root / "memory-old"
        new_memory_root = self.root / "memory-new"
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        old_command = (
            f"PSC_MEMORY_ROOT={old_memory_root} "
            f"PSC_CONFIG_ROOT={self.config_root} "
            f"{old_memory_root}/hooks/.venv/bin/python {old_memory_root}/hooks/claude_session_end.py"
        )
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionEnd": [
                            {
                                "matcher": "",
                                "hooks": [{"type": "command", "command": old_command, "timeout": 10}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_install(
            [
                "--memory-root", str(new_memory_root),
                "--config-root", str(self.config_root),
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        settings = json.loads(settings_path.read_text())
        commands = [
            h["command"]
            for entry in settings.get("hooks", {}).get("SessionEnd", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(
            [cmd for cmd in commands if "claude_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={new_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{new_memory_root}/hooks/.venv/bin/python {new_memory_root}/hooks/claude_session_end.py"
            ],
        )

    # ------------------------------------------------------------------
    # Full install — Codex config
    # ------------------------------------------------------------------

    def test_full_install_writes_codex_hooks_with_stop_and_subagent(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        self.assertTrue(hooks_path.exists(), "codex hooks.json not created")
        hooks = json.loads(hooks_path.read_text())
        self.assertIn("Stop", hooks.get("hooks", {}))
        self.assertIn("SubagentStop", hooks.get("hooks", {}))

        stop_commands = [
            h["command"]
            for entry in hooks["hooks"]["Stop"]
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertTrue(
            any("codex_session_end.py" in c for c in stop_commands),
            f"codex_session_end.py not in Stop hooks: {stop_commands}",
        )
        self.assertTrue(
            any(f"PSC_MEMORY_ROOT={self.memory_root}" in c for c in stop_commands),
            f"PSC_MEMORY_ROOT override missing from Codex Stop hooks: {stop_commands}",
        )
        self.assertTrue(
            any(f"PSC_CONFIG_ROOT={self.config_root}" in c for c in stop_commands),
            f"PSC_CONFIG_ROOT override missing from Codex Stop hooks: {stop_commands}",
        )
        subagent_commands = [
            h["command"]
            for entry in hooks["hooks"]["SubagentStop"]
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertTrue(
            any("--subagent" in c for c in subagent_commands),
            f"--subagent not in SubagentStop commands: {subagent_commands}",
        )

    def test_full_install_writes_codex_sessionstart_entry(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        deployed = self.memory_root / "hooks" / "codex_session_start.py"
        self.assertTrue(deployed.exists(), "codex_session_start.py not deployed")

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        session_start = hooks.get("hooks", {}).get("SessionStart", [])
        self.assertTrue(len(session_start) > 0, "No SessionStart entries")

        matchers = [entry.get("matcher") for entry in session_start]
        self.assertIn(
            "startup|clear|compact", matchers,
            f"SessionStart matcher startup|clear|compact missing: {matchers}",
        )

        start_commands = [
            h["command"]
            for entry in session_start
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertTrue(
            any("codex_session_start.py" in c for c in start_commands),
            f"codex_session_start.py not in SessionStart hooks: {start_commands}",
        )
        self.assertTrue(
            any(f"PSC_MEMORY_ROOT={self.memory_root}" in c for c in start_commands),
            f"PSC_MEMORY_ROOT override missing from SessionStart hooks: {start_commands}",
        )
        managed_by = [
            h.get("managedBy")
            for entry in session_start
            for h in entry.get("hooks", [])
        ]
        self.assertIn("paulsha-memory", managed_by)

    def test_full_install_does_not_duplicate_codex_sessionstart_entry(self):
        _run_install(self.base_args)
        _run_install(self.base_args)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        start_commands = [
            h["command"]
            for entry in hooks["hooks"]["SessionStart"]
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        matching = [c for c in start_commands if "codex_session_start.py" in c]
        self.assertEqual(
            len(matching), 1, f"Duplicate codex SessionStart entries: {matching}"
        )

    def test_full_install_does_not_duplicate_codex_hook_entries(self):
        _run_install(self.base_args)
        _run_install(self.base_args)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks["hooks"]["Stop"]
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        matching = [c for c in stop_commands if "codex_session_end.py" in c]
        self.assertEqual(len(matching), 1, f"Duplicate codex Stop entries: {matching}")

    def test_full_install_preserves_unrelated_codex_hook_with_matching_substring(self):
        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_stop = "/tmp/custom-wrapper codex_session_end.py --keep"
        unrelated_subagent = "/tmp/custom-wrapper codex_session_end.py --keep --subagent"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"matcher": ".*", "hooks": [{"type": "command", "command": unrelated_stop}]}
                        ],
                        "SubagentStop": [
                            {
                                "matcher": ".*",
                                "hooks": [{"type": "command", "command": unrelated_subagent}],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_install(self.base_args)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        subagent_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("SubagentStop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertIn(unrelated_stop, stop_commands)
        self.assertIn(unrelated_subagent, subagent_commands)

    def test_full_install_fails_without_overwriting_invalid_codex_hooks(self):
        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        original = '{"hooks": invalid-json}'
        hooks_path.write_text(original, encoding="utf-8")

        result = _run_install(self.base_args)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hooks_path.read_text(encoding="utf-8"), original)

    def test_reinstall_updates_codex_hook_commands_to_new_memory_root(self):
        first_memory_root = self.root / "memory-first"
        second_memory_root = self.root / "memory-second"
        first_args = [
            "--memory-root", str(first_memory_root),
            "--config-root", str(self.config_root),
            "--repo-root", self.repo_root,
            "--skip-venv",
        ]
        second_args = [
            "--memory-root", str(second_memory_root),
            "--config-root", str(self.config_root),
            "--repo-root", self.repo_root,
            "--skip-venv",
        ]

        self.assertEqual(_run_install(first_args).returncode, 0)
        self.assertEqual(_run_install(second_args).returncode, 0)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        subagent_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("SubagentStop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(
            [cmd for cmd in stop_commands if "codex_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={second_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{second_memory_root}/hooks/.venv/bin/python {second_memory_root}/hooks/codex_session_end.py"
            ],
        )
        self.assertEqual(
            [cmd for cmd in subagent_commands if "codex_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={second_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{second_memory_root}/hooks/.venv/bin/python {second_memory_root}/hooks/codex_session_end.py --subagent"
            ],
        )

    def test_reinstall_replaces_legacy_prefixed_codex_hooks_without_managed_marker(self):
        old_memory_root = self.root / "memory-old"
        new_memory_root = self.root / "memory-new"
        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        old_stop = (
            f"PSC_MEMORY_ROOT={old_memory_root} "
            f"PSC_CONFIG_ROOT={self.config_root} "
            f"{old_memory_root}/hooks/.venv/bin/python {old_memory_root}/hooks/codex_session_end.py"
        )
        old_subagent = old_stop + " --subagent"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": old_stop,
                                        "statusMessage": "paulsha-memory: capturing turn snapshot",
                                    }
                                ],
                            }
                        ],
                        "SubagentStop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": old_subagent,
                                        "statusMessage": "paulsha-memory: capturing subagent snapshot",
                                    }
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_install(
            [
                "--memory-root", str(new_memory_root),
                "--config-root", str(self.config_root),
                "--repo-root", self.repo_root,
                "--skip-venv",
            ]
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        subagent_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("SubagentStop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(
            [cmd for cmd in stop_commands if "codex_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={new_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{new_memory_root}/hooks/.venv/bin/python {new_memory_root}/hooks/codex_session_end.py"
            ],
        )
        self.assertEqual(
            [cmd for cmd in subagent_commands if "codex_session_end.py" in cmd],
            [
                f"PSC_MEMORY_ROOT={new_memory_root} "
                f"PSC_CONFIG_ROOT={self.config_root} "
                f"{new_memory_root}/hooks/.venv/bin/python {new_memory_root}/hooks/codex_session_end.py --subagent"
            ],
        )

    # ------------------------------------------------------------------
    # Full install — Copilot config
    # ------------------------------------------------------------------

    def test_full_install_writes_copilot_hook_config(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hook_path = self.config_root / ".copilot" / "hooks" / "paulsha-memory.json"
        self.assertTrue(hook_path.exists(), "copilot paulsha-memory.json not created")
        config = json.loads(hook_path.read_text())
        self.assertEqual(config.get("version"), 1)
        session_end = config.get("hooks", {}).get("sessionEnd", [])
        self.assertTrue(len(session_end) > 0, "No sessionEnd hook entries")
        bash_cmds = [e.get("bash", "") for e in session_end]
        self.assertTrue(
            any("copilot_session_end.py" in c for c in bash_cmds),
            f"copilot_session_end.py not in bash: {bash_cmds}",
        )
        self.assertTrue(
            any(f"PSC_MEMORY_ROOT={self.memory_root}" in c for c in bash_cmds),
            f"PSC_MEMORY_ROOT override missing from Copilot hooks: {bash_cmds}",
        )
        self.assertTrue(
            any(f"PSC_CONFIG_ROOT={self.config_root}" in c for c in bash_cmds),
            f"PSC_CONFIG_ROOT override missing from Copilot hooks: {bash_cmds}",
        )

    # ------------------------------------------------------------------
    # Full install — projects.yaml
    # ------------------------------------------------------------------

    def test_full_install_writes_projects_yaml_when_absent(self):
        result = _run_install(self.base_args)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        projects_path = self.config_root / ".agents" / "config" / "projects.yaml"
        self.assertTrue(projects_path.exists(), "projects.yaml not created")
        content = projects_path.read_text(encoding="utf-8")
        self.assertIn("paulshaclaw", content)

    def test_full_install_does_not_overwrite_existing_projects_yaml(self):
        projects_path = self.config_root / ".agents" / "config" / "projects.yaml"
        projects_path.parent.mkdir(parents=True, exist_ok=True)
        projects_path.write_text("# existing content\n", encoding="utf-8")

        _run_install(self.base_args)

        self.assertEqual(projects_path.read_text(encoding="utf-8"), "# existing content\n")

    # ------------------------------------------------------------------
    # Full install — Codex trust reminder
    # ------------------------------------------------------------------

    def test_full_install_prints_codex_trust_reminder(self):
        result = _run_install(self.base_args)
        combined = result.stdout + result.stderr
        self.assertIn("/hooks", combined, "Codex /hooks trust reminder not printed")

    def test_upgrade_flag_is_accepted_as_full_install(self):
        result = _run_install(self.base_args + ["--upgrade"])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((self.memory_root / "hooks" / "install.sh").exists())

    # ------------------------------------------------------------------
    # Uninstaller
    # ------------------------------------------------------------------

    def test_uninstall_removes_managed_claude_hook_entry(self):
        _run_install(self.base_args)
        settings_path = self.config_root / ".claude" / "settings.json"
        self.assertTrue(settings_path.exists())

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        settings = json.loads(settings_path.read_text())
        commands = [
            h["command"]
            for entry in settings.get("hooks", {}).get("SessionEnd", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertFalse(
            any("claude_session_end.py" in c for c in commands),
            f"claude hook not removed: {commands}",
        )

    def test_uninstall_removes_managed_claude_sessionstart_and_precompact_entries(self):
        """Uninstall should remove managed SessionStart and PreCompact entries for Claude."""
        _run_install(self.base_args)
        settings_path = self.config_root / ".claude" / "settings.json"
        self.assertTrue(settings_path.exists())

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        settings = json.loads(settings_path.read_text())
        # Collect all commands across SessionStart and PreCompact
        cmds = []
        for ev in ("SessionStart", "PreCompact"):
            for entry in settings.get("hooks", {}).get(ev, []):
                for h in entry.get("hooks", []):
                    if "command" in h:
                        cmds.append(h["command"])

        self.assertFalse(any("claude_session_start.py" in c for c in cmds), f"claude_session_start.py not removed: {cmds}")
        self.assertFalse(any("claude_precompact.py" in c for c in cmds), f"claude_precompact.py not removed: {cmds}")

    def test_uninstall_preserves_unrelated_claude_hooks_in_same_entry(self):
        settings_path = self.config_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_command = "/tmp/custom-wrapper --delegate claude_session_end.py --keep"
        managed_command = (
            f"{self.memory_root}/hooks/.venv/bin/python "
            f"{self.memory_root}/hooks/claude_session_end.py"
        )
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionEnd": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": unrelated_command},
                                    {
                                        "type": "command",
                                        "command": managed_command,
                                        "timeout": 10,
                                        "managedBy": "paulsha-memory",
                                    },
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        settings = json.loads(settings_path.read_text())
        commands = [
            h["command"]
            for entry in settings.get("hooks", {}).get("SessionEnd", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(commands, [unrelated_command])

    def test_uninstall_rejects_empty_config_root(self):
        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", "",
        ])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config-root must not be empty", result.stderr)

    def test_uninstall_rejects_filesystem_root_config_root(self):
        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", "/",
        ])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config-root must not be /", result.stderr)

    def test_uninstall_removes_managed_codex_hook_entries(self):
        _run_install(self.base_args)

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertFalse(
            any("codex_session_end.py" in c for c in stop_commands),
            f"codex hook not removed: {stop_commands}",
        )

    def test_uninstall_removes_managed_codex_sessionstart_entry(self):
        _run_install(self.base_args)

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        start_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("SessionStart", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertFalse(
            any("codex_session_start.py" in c for c in start_commands),
            f"codex SessionStart hook not removed: {start_commands}",
        )

    def test_uninstall_preserves_unrelated_codex_hooks_in_same_entry(self):
        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_stop = "/tmp/custom-wrapper codex_session_end.py --keep"
        unrelated_subagent = "/tmp/custom-wrapper codex_session_end.py --keep --subagent"
        managed_stop = (
            f"{self.memory_root}/hooks/.venv/bin/python "
            f"{self.memory_root}/hooks/codex_session_end.py"
        )
        managed_subagent = managed_stop + " --subagent"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {"type": "command", "command": unrelated_stop},
                                    {
                                        "type": "command",
                                        "command": managed_stop,
                                        "statusMessage": "paulsha-memory: capturing turn snapshot",
                                        "managedBy": "paulsha-memory",
                                    },
                                ],
                            }
                        ],
                        "SubagentStop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {"type": "command", "command": unrelated_subagent},
                                    {
                                        "type": "command",
                                        "command": managed_subagent,
                                        "statusMessage": "paulsha-memory: capturing subagent snapshot",
                                        "managedBy": "paulsha-memory",
                                    },
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        hooks = json.loads(hooks_path.read_text())
        stop_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        subagent_commands = [
            h["command"]
            for entry in hooks.get("hooks", {}).get("SubagentStop", [])
            for h in entry.get("hooks", [])
            if "command" in h
        ]
        self.assertEqual(stop_commands, [unrelated_stop])
        self.assertEqual(subagent_commands, [unrelated_subagent])

    def test_uninstall_removes_copilot_hook_file(self):
        _run_install(self.base_args)
        hook_path = self.config_root / ".copilot" / "hooks" / "paulsha-memory.json"
        self.assertTrue(hook_path.exists())

        _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])

        self.assertFalse(hook_path.exists(), "copilot hook file not removed")

    def test_uninstall_preserves_inbox_content(self):
        _run_install(self.base_args)
        # Place a file in inbox
        inbox_file = self.memory_root / "inbox" / "sessions" / "keep-me.md"
        inbox_file.parent.mkdir(parents=True, exist_ok=True)
        inbox_file.write_text("preserved", encoding="utf-8")

        _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])

        self.assertTrue(inbox_file.exists(), "inbox content was deleted by uninstall")

    def test_uninstall_preserves_projects_yaml(self):
        _run_install(self.base_args)
        projects_path = self.config_root / ".agents" / "config" / "projects.yaml"
        self.assertTrue(projects_path.exists())
        original = projects_path.read_text(encoding="utf-8")

        _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])

        self.assertTrue(projects_path.exists(), "projects.yaml removed by uninstall")
        self.assertEqual(projects_path.read_text(encoding="utf-8"), original)

    def test_uninstall_removes_legacy_prefixed_codex_hook_without_managed_marker(self):
        hooks_path = self.config_root / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        old_memory_root = self.root / "memory-old"
        old_stop = (
            f"PSC_MEMORY_ROOT={old_memory_root} "
            f"PSC_CONFIG_ROOT={self.config_root} "
            f"{old_memory_root}/hooks/.venv/bin/python {old_memory_root}/hooks/codex_session_end.py"
        )
        old_subagent = old_stop + " --subagent"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": old_stop,
                                        "statusMessage": "paulsha-memory: capturing turn snapshot",
                                    }
                                ],
                            }
                        ],
                        "SubagentStop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": old_subagent,
                                        "statusMessage": "paulsha-memory: capturing subagent snapshot",
                                    }
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        result = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        hooks = json.loads(hooks_path.read_text())
        self.assertEqual(hooks.get("hooks", {}).get("Stop", []), [])
        self.assertEqual(hooks.get("hooks", {}).get("SubagentStop", []), [])

    def test_uninstall_is_idempotent(self):
        _run_install(self.base_args)
        r1 = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        r2 = _run_uninstall([
            "--memory-root", str(self.memory_root),
            "--config-root", str(self.config_root),
        ])
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)

    # ------------------------------------------------------------------
    # Packaging: editable install surface (pyproject.toml presence)
    # ------------------------------------------------------------------

    def test_pyproject_toml_exists_for_editable_install(self):
        pyproject = REPO_ROOT / "pyproject.toml"
        self.assertTrue(
            pyproject.exists(),
            f"pyproject.toml missing — editable install will not work: {pyproject}",
        )

    def test_full_install_creates_importable_venv(self):
        result = _run_install(
            [
                "--memory-root", str(self.memory_root),
                "--config-root", str(self.config_root),
                "--repo-root", self.repo_root,
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        venv_python = self.memory_root / "hooks" / ".venv" / "bin" / "python"
        self.assertTrue(venv_python.exists(), f"venv python missing: {venv_python}")
        completed = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import paulsha_hippo.importer.cli; print('IMPORT_OK')",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("IMPORT_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
