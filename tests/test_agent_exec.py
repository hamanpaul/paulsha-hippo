from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.atomizer import agent_exec

STUB = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"
CANONICAL_FAKE_JSON = (
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw",'
    '"tags":["t1"],"body":"alpha distilled","source_fragment_indices":[0],'
    '"relations":[{"type":"mentions","entity":"MTK"}]}]'
)


class AgentExecTests(unittest.TestCase):
    def make_sandbox(self, name: str) -> Path:
        sandbox = tempfile.TemporaryDirectory(prefix=f"agent-exec-{name}-")
        self.addCleanup(sandbox.cleanup)
        return Path(sandbox.name)

    def test_fake_client_returns_canned(self):
        client = agent_exec.FakeAgentClient(CANONICAL_FAKE_JSON)
        self.assertEqual(client.run("anything"), CANONICAL_FAKE_JSON)

    def test_make_sandbox_uses_system_temp_dir(self):
        sandbox = self.make_sandbox("system-temp")
        temp_root = Path(tempfile.gettempdir()).resolve()
        self.assertEqual(
            Path(os.path.commonpath([sandbox.resolve(), temp_root])),
            temp_root,
        )

    def test_exec_client_runs_stub_and_returns_stdout(self):
        client = agent_exec.AgentExecClient([sys.executable, str(STUB)], timeout=30)
        out = client.run("hello prompt")
        self.assertEqual(out.strip(), CANONICAL_FAKE_JSON)

    def test_exec_client_missing_command_raises(self):
        client = agent_exec.AgentExecClient(["/nonexistent/bin/nope"], timeout=5)
        with self.assertRaises(agent_exec.AgentExecError):
            client.run("x")

    def test_exec_client_nonzero_exit_raises(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            timeout=5,
        )
        with self.assertRaises(agent_exec.AgentExecError):
            client.run("x")

    def test_exec_client_timeout_raises(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1,
        )
        with self.assertRaises(agent_exec.AgentExecError):
            client.run("x")

    def test_exec_client_empty_stdout_raises(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import sys; sys.stdin.read(); print('', end='')"],
            timeout=5,
        )
        with self.assertRaises(agent_exec.AgentExecError):
            client.run("x")

    def test_caching_client_reuses_by_prompt_hash(self):
        calls = {"n": 0}

        class Counting(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                calls["n"] += 1
                return CANONICAL_FAKE_JSON

        cache_dir = self.make_sandbox("cache-hit")
        cached = agent_exec.CachingAgentClient(Counting(), cache_dir)
        self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)
        self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)
        self.assertEqual(calls["n"], 1)

    def test_caching_client_reuses_by_explicit_cache_key(self):
        calls = {"n": 0}

        class Counting(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                calls["n"] += 1
                return CANONICAL_FAKE_JSON

        cache_dir = self.make_sandbox("cache-key-hit")
        cached = agent_exec.CachingAgentClient(Counting(), cache_dir)
        self.assertEqual(cached.run_cached("prompt one", "session__hash"), CANONICAL_FAKE_JSON)
        self.assertEqual(cached.run_cached("prompt two", "session__hash"), CANONICAL_FAKE_JSON)
        self.assertEqual(calls["n"], 1)

    def test_caching_client_corrupt_entry_is_miss(self):
        cache_dir = self.make_sandbox("cache-corrupt")
        cached = agent_exec.CachingAgentClient(
            agent_exec.FakeAgentClient(CANONICAL_FAKE_JSON),
            cache_dir,
        )
        cached.cache_path_for("p").write_bytes(b"\xff")
        self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)

    def test_caching_client_empty_entry_is_miss(self):
        calls = {"n": 0}

        class Counting(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                calls["n"] += 1
                return CANONICAL_FAKE_JSON

        cache_dir = self.make_sandbox("cache-empty")
        cached = agent_exec.CachingAgentClient(Counting(), cache_dir)
        cached.cache_path_for("p").write_text("", encoding="utf-8")
        self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)
        self.assertEqual(calls["n"], 1)

    def test_caching_client_unreadable_entry_is_miss(self):
        calls = {"n": 0}

        class Counting(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                calls["n"] += 1
                return CANONICAL_FAKE_JSON

        cache_dir = self.make_sandbox("cache-unreadable")
        cached = agent_exec.CachingAgentClient(Counting(), cache_dir)
        cached.cache_path_for("p").write_text("stale", encoding="utf-8")

        with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
            self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)
        self.assertEqual(calls["n"], 1)

    def test_caching_client_writes_via_temp_file_replace(self):
        cache_dir = self.make_sandbox("cache-atomic")
        cached = agent_exec.CachingAgentClient(
            agent_exec.FakeAgentClient(CANONICAL_FAKE_JSON),
            cache_dir,
        )
        cache_path = cached.cache_path_for("p")
        temp_path = cache_path.with_name(f".{cache_path.name}.tmp")
        write_targets: list[Path] = []
        replace_calls: list[tuple[Path, Path]] = []
        real_write_text = Path.write_text
        real_replace = Path.replace

        def write_spy(path: Path, *args, **kwargs):
            write_targets.append(path)
            return real_write_text(path, *args, **kwargs)

        def replace_spy(path: Path, target: Path, *args, **kwargs):
            replace_calls.append((path, target))
            return real_replace(path, target, *args, **kwargs)

        with (
            mock.patch.object(Path, "write_text", autospec=True, side_effect=write_spy),
            mock.patch.object(Path, "replace", autospec=True, side_effect=replace_spy),
        ):
            self.assertEqual(cached.run("p"), CANONICAL_FAKE_JSON)

        self.assertEqual(write_targets, [temp_path])
        self.assertEqual(replace_calls, [(temp_path, cache_path)])
        self.assertFalse(temp_path.exists())
        self.assertEqual(cache_path.read_text(encoding="utf-8"), CANONICAL_FAKE_JSON)

    def test_caching_client_clear_cache_key_removes_entry(self):
        cache_dir = self.make_sandbox("cache-clear")
        cached = agent_exec.CachingAgentClient(
            agent_exec.FakeAgentClient(CANONICAL_FAKE_JSON),
            cache_dir,
        )
        cache_path = cached.cache_path_for_key("session__hash")
        cached.run_cached("p", "session__hash")
        self.assertTrue(cache_path.exists())

        cached.clear_cache_key("session__hash")

        self.assertFalse(cache_path.exists())

    def test_exec_client_env_override_passed_to_subprocess(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c",
             "import os,sys; sys.stdin.read(); print(os.environ.get('CLAUDE_CODE_MAX_OUTPUT_TOKENS',''))"],
            timeout=5,
            env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192"},
        )
        self.assertEqual(client.run("x").strip(), "8192")

    def test_exec_client_no_env_inherits_parent(self):
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "4242"
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c",
             "import os,sys; sys.stdin.read(); print(os.environ.get('CLAUDE_CODE_MAX_OUTPUT_TOKENS',''))"],
            timeout=5,
        )
        self.assertEqual(client.run("x").strip(), "4242")


if __name__ == "__main__":
    unittest.main()
