"""第二刀元件測試：init/doctor/install service/supervise 與蒸餾 backend。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value="/fake/bin/claude"):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 0)
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertIn("backend: claude-headless", cfg.read_text(encoding="utf-8"))
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            body = override.read_text(encoding="utf-8")
            # #15：argv[0] 絕對路徑化——systemd 環境沒有 NVM PATH，裸命令找不到
            self.assertIn("- /fake/bin/claude", body)
            self.assertIn("- -p", body)
            self.assertNotIn("\n    - claude\n", body)

    def test_init_claude_headless_fails_when_backend_missing(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 2)
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            self.assertFalse(override.exists())

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


class ResolveBackendArgvTests(unittest.TestCase):
    def test_resolves_bare_command_to_absolute(self):
        with mock.patch.object(ops.shutil, "which", return_value="/usr/bin/claude"):
            self.assertEqual(
                ops.resolve_backend_argv(["claude", "-p"]), ["/usr/bin/claude", "-p"]
            )

    def test_missing_command_raises_backend_unavailable(self):
        with mock.patch.object(ops.shutil, "which", return_value=None):
            with self.assertRaises(ops.BackendUnavailableError):
                ops.resolve_backend_argv(["nope-cmd"])

    def test_error_is_value_error_subclass(self):
        self.assertTrue(issubclass(ops.BackendUnavailableError, ValueError))

    def test_empty_argv_raises(self):
        with self.assertRaises(ops.BackendUnavailableError):
            ops.resolve_backend_argv([])


class DoctorTests(unittest.TestCase):
    _PROBE_OK = ("- distiller backend：✓ mocked", False)

    def test_conflicting_roots_fail(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/b"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=self._PROBE_OK):
            self.assertEqual(ops.run_doctor(), 1)

    def test_consistent_roots_pass(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=self._PROBE_OK):
            self.assertEqual(ops.run_doctor(), 0)


class DoctorBackendProbeTests(unittest.TestCase):
    _ENV = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}

    def _fake_cfg(self, **overrides):
        base = dict(
            agent_exec_backend="custom-argv",
            agent_exec_command=("claude", "-p"),
            agent_exec_base_url="",
            default_promoter="llm",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_probe_fails_when_bare_command_unresolvable_in_service_path(self):
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(self._fake_cfg(), "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_effective_path_env",
                               return_value="/usr/bin:/bin"), \
             mock.patch.object(ops.shutil, "which", return_value=None):
            self.assertEqual(ops.run_doctor(), 1)

    def test_probe_passes_with_absolute_executable(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "claude"
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
            cfg = self._fake_cfg(agent_exec_command=(str(exe), "-p"))
            with mock.patch.dict("os.environ", self._ENV), \
                 mock.patch("paulsha_hippo.atomizer.config.load_config",
                            return_value=(cfg, "h")), \
                 mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                            side_effect=lambda command: tuple(command)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"):
                self.assertEqual(ops.run_doctor(), 0)

    def test_probe_reports_openai_compatible_as_delegated(self):
        cfg = self._fake_cfg(agent_exec_backend="openai-compatible",
                             agent_exec_base_url="http://127.0.0.1:11434")
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")):
            self.assertEqual(ops.run_doctor(), 0)

    def test_service_effective_path_falls_back_without_systemd(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=OSError("no systemctl")):
            self.assertEqual(ops._service_effective_path_env(), "/usr/local/bin:/usr/bin:/bin")

    def _doctor_with_command(self, command: tuple[str, ...]) -> int:
        cfg = self._fake_cfg(agent_exec_command=command)
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_effective_path_env",
                               return_value="/usr/bin:/bin"):
            return ops.run_doctor()

    def test_probe_fails_when_env_child_runtime_missing(self):
        # review F3 反例：argv[0]（/usr/bin/env）is_file()+X_OK 全過，但實跑 exit 127。
        # 只驗 argv[0] 的舊 probe 在此錯誤綠燈 → recovery gate 誤判 → requeue 再入失敗鏈。
        self.assertEqual(
            self._doctor_with_command(("/usr/bin/env", "definitely-no-such-runtime")),
            1,
        )

    def test_probe_fails_when_shebang_interpreter_missing(self):
        # argv[0] 存在且有 executable bit，但 shebang interpreter 缺失（#15 NVM 根因形狀）
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "claude"
            exe.write_text("#!/no/such/interpreter-xyz\n", encoding="utf-8")
            exe.chmod(0o755)
            self.assertEqual(self._doctor_with_command((str(exe), "-p")), 1)

    def test_probe_passes_on_nonzero_business_exit(self):
        # exec 鏈可用、程式自身非零退出（非 126/127）→ 不是 backend 缺失，PASS
        self.assertEqual(self._doctor_with_command(("/bin/sh", "-c", "exit 3")), 0)

    def test_probe_timeout_counts_as_executable(self):
        with mock.patch.object(ops, "_PROBE_TIMEOUT_SECS", 0.2):
            self.assertEqual(self._doctor_with_command(("/bin/sleep", "5")), 0)


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
        timer_active = mock.Mock(return_value=False)
        with mock.patch.object(ops.time, "sleep") as sleep:
            rc = ops.run_dream_supervise(
                interval=7, once=True, runner=lambda: calls.append(1),
                timer_active=timer_active,
            )
        self.assertEqual(rc, 0)
        timer_active.assert_called_once_with()
        sleep.assert_called_with(7)
        self.assertEqual(calls, [1])

    def test_supervise_survives_single_round_failure(self):
        def boom():
            raise RuntimeError("x")

        timer_active = mock.Mock(return_value=False)
        with mock.patch.object(ops.time, "sleep") as sleep:
            self.assertEqual(
                ops.run_dream_supervise(
                    interval=1, once=True, runner=boom, timer_active=timer_active
                ),
                0,
            )
        timer_active.assert_called_once_with()
        sleep.assert_called_with(1)

    def test_supervise_defers_when_timer_active(self):
        calls = []
        rc = ops.run_dream_supervise(
            interval=1, once=True, runner=lambda: calls.append(1),
            timer_active=lambda: True,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_supervise_runs_when_timer_inactive(self):
        calls = []
        timer_active = mock.Mock(return_value=False)
        with mock.patch.object(ops.time, "sleep") as sleep:
            rc = ops.run_dream_supervise(
                interval=1, once=True, runner=lambda: calls.append(1),
                timer_active=timer_active,
            )
        self.assertEqual(rc, 0)
        timer_active.assert_called_once_with()
        sleep.assert_called_with(1)
        self.assertEqual(calls, [1])

    def test_dream_timer_active_false_when_systemctl_permission_denied(self):
        # systemctl 存在但無權限執行時應 fallback 為 False，不得讓例外往外傳
        with mock.patch.object(ops.subprocess, "run", side_effect=PermissionError("denied")):
            self.assertFalse(ops._dream_timer_active())


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

    def test_execstart_uses_current_interpreter_not_env_python(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(ops, "_systemd_user_available", return_value=True), \
                 mock.patch.object(ops.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="Linger=yes")
                ops.run_install_service(enable=False, home_dir=tmp)
            body = (Path(tmp) / ".config" / "systemd" / "user" / "paulsha-hippo-dream.service").read_text(encoding="utf-8")
            # pipx / venv 隔離安裝下，ExecStart 必須綁定當前 interpreter（sys.executable），
            # 不能用 /usr/bin/env python3（全域 python 會 import 不到 paulsha_hippo）
            self.assertIn(f"ExecStart={sys.executable} -m paulsha_hippo.cli dream run", body)
            self.assertNotIn("/usr/bin/env python3", body)


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


class FixBackendMigrationTests(unittest.TestCase):
    _OVERRIDE = (
        'schema_version: "1"\n'
        "agent_exec:\n"
        "  command:\n"
        "    - claude\n"
        "    - -p\n"
    )

    def _env(self, tmp: str) -> dict[str, str]:
        return {
            "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
            "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
            "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            "PSC_MEMORY_ROOT": f"{tmp}/memory",
        }

    def _write_override(self, tmp: str) -> Path:
        override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(self._OVERRIDE, encoding="utf-8")
        return override

    def _real_exe(self, tmp: str) -> Path:
        exe = Path(tmp) / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def test_fix_backend_rewrites_bare_command_and_backs_up(self):
        with TemporaryDirectory() as tmp:
            override = self._write_override(tmp)
            exe = self._real_exe(tmp)

            def fake_which(cmd, path=None):
                # 只攔 claude：service-effective PATH（path 給定）→ 解析不到；互動環境 → 找得到。
                # systemctl 等其他查詢一律 None，讓 doctor 走無 systemd fallback（確定性）。
                if cmd != "claude":
                    return None
                return None if path is not None else str(exe)

            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", side_effect=fake_which):
                rc = ops.run_doctor(fix_backend=True)

            self.assertEqual(rc, 0)
            body = override.read_text(encoding="utf-8")
            self.assertIn(f"    - {exe}\n", body)
            self.assertNotIn("\n    - claude\n", body)
            backup = override.with_name(override.name + ".bak")
            self.assertIn("    - claude\n", backup.read_text(encoding="utf-8"))

    def test_fix_backend_is_idempotent_on_second_run(self):
        with TemporaryDirectory() as tmp:
            override = self._write_override(tmp)
            exe = self._real_exe(tmp)

            def fake_which(cmd, path=None):
                if cmd != "claude":
                    return None
                return None if path is not None else str(exe)

            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", side_effect=fake_which):
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)
                first_body = override.read_text(encoding="utf-8")
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)
                second_body = override.read_text(encoding="utf-8")

            self.assertEqual(first_body, second_body)
            backup = override.with_name(override.name + ".bak")
            # 第二輪 no-op：備份仍是第一輪存下的原始裸命令版
            self.assertIn("    - claude\n", backup.read_text(encoding="utf-8"))

    def test_fix_backend_without_override_is_noop(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_probe_backend_service_effective",
                                   return_value=("- distiller backend：✓ mocked", False)):
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)

    def test_fix_backend_unresolvable_everywhere_fails(self):
        with TemporaryDirectory() as tmp:
            self._write_override(tmp)
            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                self.assertEqual(ops.run_doctor(fix_backend=True), 1)
