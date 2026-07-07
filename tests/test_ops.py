"""第二刀元件測試：init/doctor/install service/supervise 與蒸餾 backend。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import ops, paths
from paulsha_hippo.atomizer.agent_exec import AgentExecError, HttpAgentClient


class InitTests(unittest.TestCase):
    def test_init_claude_headless_writes_config_and_override(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 0)
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertIn("backend: claude-headless", cfg.read_text(encoding="utf-8"))
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            body = override.read_text(encoding="utf-8")
            self.assertIn("- claude", body)
            self.assertIn("- -p", body)

    def test_init_openai_compatible_requires_base_url(self):
        rc = ops.run_init(memory_root=None, backend="openai-compatible",
                          base_url=None, api_key_env=None, model=None, assume_yes=True)
        self.assertEqual(rc, 2)

    def test_init_never_overwrites_existing_config(self):
        with TemporaryDirectory() as tmp:
            env = {"HIPPO_CONFIG_ROOT": tmp, "PSC_CONFIG_ROOT": f"{tmp}/l"}
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("memory_root: /keep/me\n", encoding="utf-8")
            with mock.patch.dict("os.environ", env):
                ops.run_init(memory_root="/x", backend="custom-argv",
                             base_url=None, api_key_env=None, model=None, assume_yes=False)
            self.assertIn("/keep/me", cfg.read_text(encoding="utf-8"))


class DoctorTests(unittest.TestCase):
    def test_conflicting_roots_fail(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/b"}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(ops.run_doctor(), 1)

    def test_consistent_roots_pass(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(ops.run_doctor(), 0)


class InstallServiceTests(unittest.TestCase):
    def test_installs_renamed_units_when_systemd_available(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(ops, "_systemd_user_available", return_value=True), \
                 mock.patch.object(ops.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="Linger=yes")
                rc = ops.run_install_service(enable=False, home_dir=tmp)
            self.assertEqual(rc, 0)
            unit = Path(tmp) / ".config" / "systemd" / "user" / "paulsha-hippo-dream.service"
            body = unit.read_text(encoding="utf-8")
            self.assertNotIn("paulsha-memory-dream", body)
            self.assertTrue((Path(tmp) / ".config" / "systemd" / "user" / "paulsha-hippo-dream.timer").is_file())

    def test_falls_back_to_supervise_hint_without_systemd(self):
        with mock.patch.object(ops, "_systemd_user_available", return_value=False):
            self.assertEqual(ops.run_install_service(enable=True, home_dir="/nonexistent-x"), 0)


class SuperviseTests(unittest.TestCase):
    def test_supervise_defers_first_run_then_invokes(self):
        calls = []
        with mock.patch.object(ops.time, "sleep") as sleep:
            rc = ops.run_dream_supervise(interval=7, once=True, runner=lambda: calls.append(1))
        self.assertEqual(rc, 0)
        sleep.assert_called_with(7)
        self.assertEqual(calls, [1])

    def test_supervise_survives_single_round_failure(self):
        def boom():
            raise RuntimeError("x")

        with mock.patch.object(ops.time, "sleep"):
            self.assertEqual(ops.run_dream_supervise(interval=1, once=True, runner=boom), 0)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        self.server.captured = {"auth": self.headers.get("Authorization"), "payload": payload}  # type: ignore[attr-defined]
        body = json.dumps({"choices": [{"message": {"content": "DISTILLED"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 靜默
        return


class HttpAgentClientTests(unittest.TestCase):
    def _serve(self):
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def test_posts_chat_completions_and_returns_content(self):
        server = self._serve()
        try:
            with mock.patch.dict("os.environ", {"HIPPO_TEST_KEY": "sk-local"}):
                client = HttpAgentClient(
                    f"http://127.0.0.1:{server.server_port}", "gemma-test",
                    api_key_env="HIPPO_TEST_KEY", timeout=10,
                )
                out = client.run("prompt-text")
            self.assertEqual(out, "DISTILLED")
            captured = server.captured  # type: ignore[attr-defined]
            self.assertEqual(captured["auth"], "Bearer sk-local")
            self.assertEqual(captured["payload"]["model"], "gemma-test")
        finally:
            server.shutdown()

    def test_unreachable_endpoint_raises_agent_exec_error(self):
        client = HttpAgentClient("http://127.0.0.1:1", "m", timeout=2)
        with self.assertRaises(AgentExecError):
            client.run("x")


class BackendConfigTests(unittest.TestCase):
    def test_openai_compatible_requires_base_url(self):
        from paulsha_hippo.atomizer import config as aconfig

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "atomizer.yaml"
            base = (Path(aconfig.DEFAULT_CONFIG_DIR) / "atomizer.yaml").read_text(encoding="utf-8")
            cfg.write_text(base + "\n", encoding="utf-8")
            override = Path(tmp) / "override.yaml"
            override.write_text(
                'schema_version: "1"\nagent_exec:\n  backend: openai-compatible\n', encoding="utf-8"
            )
            with self.assertRaises(aconfig.AtomizerConfigError):
                aconfig.load_config(default_dir=tmp, override_path=override)

    def test_claude_headless_preset_config_valid(self):
        from paulsha_hippo.atomizer import config as aconfig

        with TemporaryDirectory() as tmp:
            override = Path(tmp) / "override.yaml"
            override.write_text(
                'schema_version: "1"\nagent_exec:\n  command:\n    - claude\n    - -p\n',
                encoding="utf-8",
            )
            config, _ = aconfig.load_config(override_path=override)
            self.assertEqual(tuple(config.agent_exec_command), ("claude", "-p"))


if __name__ == "__main__":
    unittest.main()


class InstallServiceUnitContentTests(unittest.TestCase):
    def test_rendered_unit_has_no_legacy_module_or_memory_prefix(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(ops, "_systemd_user_available", return_value=True), \
                 mock.patch.object(ops.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="Linger=yes")
                ops.run_install_service(enable=False, home_dir=tmp)
            body = (Path(tmp) / ".config" / "systemd" / "user" / "paulsha-hippo-dream.service").read_text(encoding="utf-8")
            self.assertNotIn("paulshaclaw.memory", body)
            self.assertNotIn("cli memory ", body)
            self.assertIn("paulsha_hippo.cli dream run", body)


class InstallHooksResolverTests(unittest.TestCase):
    def test_install_hooks_passes_resolved_memory_root(self):
        env = {"HIPPO_MEMORY_ROOT": "/resolved/from/env"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            ops.run_install_hooks(memory_root=None, repo_root=None)
        argv = run.call_args[0][0]
        self.assertIn("--memory-root", argv)
        self.assertEqual(argv[argv.index("--memory-root") + 1], "/resolved/from/env")
