"""第二刀元件測試：init/doctor/install service/supervise 與蒸餾 backend。"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
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
            agent_exec_model="test-model",
            agent_exec_api_key_env="",
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
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin"}), \
             mock.patch.object(ops.shutil, "which", return_value=None):
            self.assertEqual(ops.run_doctor(), 1)

    def test_probe_passes_with_absolute_executable(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "claude"
            # fail-closed 判定下 PASS 需 exit 0＋非空回應（不只 exec 得起來）
            exe.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            exe.chmod(0o755)
            cfg = self._fake_cfg(agent_exec_command=(str(exe), "-p"))
            with mock.patch.dict("os.environ", self._ENV), \
                 mock.patch("paulsha_hippo.atomizer.config.load_config",
                            return_value=(cfg, "h")), \
                 mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                            side_effect=lambda command: tuple(command)), \
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value={"PATH": "/usr/bin:/bin"}):
                self.assertEqual(ops.run_doctor(), 0)

    def test_probe_openai_compatible_success_with_live_endpoint(self):
        # openai-compatible 不再「PR-D 接手」綠燈——實際打 /v1/chat/completions
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = self._fake_cfg(
                agent_exec_backend="openai-compatible",
                agent_exec_base_url=f"http://127.0.0.1:{server.server_port}",
            )
            with mock.patch.dict("os.environ", self._ENV), \
                 mock.patch("paulsha_hippo.atomizer.config.load_config",
                            return_value=(cfg, "h")), \
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value={"PATH": "/usr/bin:/bin"}):
                self.assertEqual(ops.run_doctor(), 0)
        finally:
            server.shutdown()

    def test_probe_openai_compatible_unreachable_fails_closed(self):
        # 端點不可達（連線拒絕）→ FAIL；早前版本此情境仍 exit 0（恢復 gate 誤判）
        cfg = self._fake_cfg(
            agent_exec_backend="openai-compatible",
            agent_exec_base_url="http://127.0.0.1:1",
        )
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin"}):
            self.assertEqual(ops.run_doctor(), 1)

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
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin"}):
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

    def test_probe_fails_on_nonzero_business_exit(self):
        # fail-closed：認證／model／quota／config 錯誤都以非零 exit 呈現——
        # 一律 FAIL，否則恢復 gate 綠燈後 requeue 立即再度失敗或 parked。
        self.assertEqual(self._doctor_with_command(("/bin/sh", "-c", "exit 3")), 1)

    def test_probe_fails_on_timeout(self):
        # backend hang（上游卡住）≠ 健康；timeout fail-closed
        with mock.patch.object(ops, "_PROBE_TIMEOUT_SECS", 0.2):
            self.assertEqual(self._doctor_with_command(("/bin/sleep", "5")), 1)

    def test_probe_fails_on_empty_output(self):
        # exit 0 但無可解析回應（空輸出）→ FAIL
        self.assertEqual(self._doctor_with_command(("/bin/sh", "-c", "exit 0")), 1)

    def test_probe_passes_when_smoke_prompt_answered(self):
        # cat 把 stdin 的 smoke prompt 原樣回吐：exit 0＋非空輸出 → PASS，
        # 同時驗證 prompt 確實經 stdin 餵入（比照 AgentExecClient.run）
        self.assertEqual(self._doctor_with_command(("/bin/cat",)), 0)

    def test_probe_env_carries_self_session_marker(self):
        # #7 回歸：probe 實跑 backend argv，需比照 agent_exec.AgentExecClient.run
        # 注入 HIPPO_SELF_SESSION=1——否則使用者已裝的 SessionEnd/PreCompact hooks
        # 會把 doctor/--fix-backend 的探測當真實 session 寫入 queue（遞迴自捕捉）。
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "env.txt"
            fake_backend = Path(tmp) / "claude"
            fake_backend.write_text(
                f'#!/bin/sh\nprintf "%s" "${{HIPPO_SELF_SESSION:-MISSING}}" > "{out}"\necho ok\n',
                encoding="utf-8",
            )
            fake_backend.chmod(0o755)
            ok, _ = ops._exec_probe_service_effective(
                [str(fake_backend), "-p"], {"PATH": "/usr/bin:/bin"})
            self.assertTrue(ok)
            self.assertEqual(out.read_text(encoding="utf-8"), "1")

    def _key_gated_backend(self, tmp: str, var: str) -> Path:
        """假 backend：指定 env var 缺席即 exit 1（模擬認證必須的 API key）。"""
        exe = Path(tmp) / "claude"
        exe.write_text(
            f'#!/bin/sh\nif [ -z "${{{var}}}" ]; then echo "missing {var}" >&2; exit 1; fi\n'
            "echo ok\n",
            encoding="utf-8",
        )
        exe.chmod(0o755)
        return exe

    def _doctor_with_key_gated_backend(self, exe: Path, *, shell_env: dict[str, str],
                                       manager_env: dict[str, str] | None) -> int:
        cfg = self._fake_cfg(agent_exec_command=(str(exe), "-p"))
        with mock.patch.dict("os.environ", {**self._ENV, **shell_env}), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value=manager_env):
            return ops.run_doctor()

    def test_probe_fails_when_api_key_only_in_interactive_shell(self):
        # Codex 複驗 B1 主方向：API key 只 export 在互動 shell、manager env 沒有
        # → probe 不得誤判健康（否則 requeue 後 dream service 仍認證失敗再度 parked）。
        with TemporaryDirectory() as tmp:
            exe = self._key_gated_backend(tmp, "HIPPO_PROBE_FAKE_KEY")
            self.assertEqual(
                self._doctor_with_key_gated_backend(
                    exe,
                    shell_env={"HIPPO_PROBE_FAKE_KEY": "sk-shell-only"},
                    manager_env={"PATH": "/usr/bin:/bin"},
                ),
                1,
            )

    def test_probe_passes_when_api_key_only_in_manager_env(self):
        # B1 反向誤判：key 只設在 manager env（environment.d／set-environment）、
        # 互動 shell 沒有 → 服務實際可用，probe 不得誤判故障。
        self.assertNotIn("HIPPO_PROBE_FAKE_KEY", os.environ)
        with TemporaryDirectory() as tmp:
            exe = self._key_gated_backend(tmp, "HIPPO_PROBE_FAKE_KEY")
            self.assertEqual(
                self._doctor_with_key_gated_backend(
                    exe,
                    shell_env={},
                    manager_env={"PATH": "/usr/bin:/bin",
                                 "HIPPO_PROBE_FAKE_KEY": "sk-manager"},
                ),
                0,
            )

    def test_probe_fallback_marks_approximate_when_no_user_bus(self):
        # 無 systemd user bus（CI 等）→ fallback 現行近似（os.environ + 保守 PATH），
        # 且輸出必須明確標示「近似，非 service-effective」。
        with TemporaryDirectory() as tmp:
            exe = self._key_gated_backend(tmp, "HIPPO_PROBE_FAKE_KEY")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self._doctor_with_key_gated_backend(
                    exe,
                    shell_env={"HIPPO_PROBE_FAKE_KEY": "sk-shell-only"},
                    manager_env=None,
                )
            self.assertEqual(rc, 0)
            self.assertIn("近似", buf.getvalue())
            self.assertIn("非 service-effective", buf.getvalue())

    def _doctor_openai_with_auth_server(self, *, shell_env: dict[str, str],
                                        manager_env: dict[str, str] | None) -> int:
        server = HTTPServer(("127.0.0.1", 0), _AuthRequiredHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = self._fake_cfg(
                agent_exec_backend="openai-compatible",
                agent_exec_base_url=f"http://127.0.0.1:{server.server_port}",
                agent_exec_api_key_env="HIPPO_PROBE_HTTP_KEY",
            )
            with mock.patch.dict("os.environ", {**self._ENV, **shell_env}), \
                 mock.patch("paulsha_hippo.atomizer.config.load_config",
                            return_value=(cfg, "h")), \
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value=manager_env):
                return ops.run_doctor()
        finally:
            server.shutdown()

    def test_probe_openai_compatible_key_only_in_shell_fails(self):
        # B1 也涵蓋 openai-compatible：API key 解析須來自 manager env，
        # 不得從 doctor 所在互動 shell 的 os.environ 借到 key 而誤判健康。
        self.assertEqual(
            self._doctor_openai_with_auth_server(
                shell_env={"HIPPO_PROBE_HTTP_KEY": "sk-shell-only"},
                manager_env={"PATH": "/usr/bin:/bin"},
            ),
            1,
        )

    def test_probe_openai_compatible_key_in_manager_env_passes(self):
        self.assertNotIn("HIPPO_PROBE_HTTP_KEY", os.environ)
        self.assertEqual(
            self._doctor_openai_with_auth_server(
                shell_env={},
                manager_env={"PATH": "/usr/bin:/bin",
                             "HIPPO_PROBE_HTTP_KEY": "sk-manager"},
            ),
            0,
        )


class ServiceManagerEnvironmentTests(unittest.TestCase):
    @staticmethod
    def _completed(stdout: str, returncode: int = 0):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    def test_parses_manager_environment_lines(self):
        out = (
            "PATH=/usr/bin:/bin\n"
            "HIPPO_X=plain\n"
            "QUOTED=$'a b\\'c\\nd'\n"
            "BROKEN-LINE\n"
        )
        with mock.patch.object(ops.subprocess, "run",
                               return_value=self._completed(out)) as run:
            env = ops._service_manager_environment()
        run.assert_called_once_with(
            ["systemctl", "--user", "show-environment"], capture_output=True, text=True
        )
        self.assertEqual(
            env,
            {"PATH": "/usr/bin:/bin", "HIPPO_X": "plain", "QUOTED": "a b'c\nd"},
        )

    def test_returns_none_when_command_fails(self):
        with mock.patch.object(ops.subprocess, "run",
                               return_value=self._completed("", returncode=1)):
            self.assertIsNone(ops._service_manager_environment())

    def test_returns_none_on_oserror(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=OSError("no systemctl")):
            self.assertIsNone(ops._service_manager_environment())


class ProbeEnvironmentTests(unittest.TestCase):
    def test_manager_env_mode_excludes_interactive_environ(self):
        # B1 核心：service-effective 模式下 probe env 只來自 manager env，
        # 互動 shell 才有的變數（API key 等）不得滲入。
        with mock.patch.dict("os.environ", {"HIPPO_SHELL_ONLY_VAR": "1"}), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin", "HOME": "/home/u"}):
            env, service_effective = ops._probe_environment()
        self.assertTrue(service_effective)
        self.assertEqual(env, {"PATH": "/usr/bin:/bin", "HOME": "/home/u"})

    def test_manager_env_without_path_gets_conservative_default(self):
        with mock.patch.object(ops, "_service_manager_environment",
                               return_value={"HOME": "/home/u"}):
            env, service_effective = ops._probe_environment()
        self.assertTrue(service_effective)
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")

    def test_fallback_mode_keeps_interactive_approximation(self):
        with mock.patch.dict("os.environ", {"HIPPO_SHELL_ONLY_VAR": "1"}), \
             mock.patch.object(ops, "_service_manager_environment", return_value=None):
            env, service_effective = ops._probe_environment()
        self.assertFalse(service_effective)
        self.assertEqual(env["HIPPO_SHELL_ONLY_VAR"], "1")
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")


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


class _AuthRequiredHandler(BaseHTTPRequestHandler):
    """缺 Authorization header 即 401 的端點（模擬需認證的 openai-compatible 服務）。"""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.headers.get("Authorization"):
            body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": "missing api key"}).encode()
            self.send_response(401)
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

    def test_env_override_scopes_api_key_lookup(self):
        # B1：doctor probe 注入 service-effective env——key 從注入的 env 解析
        self.assertNotIn("HIPPO_PROBE_DIRECT_KEY", os.environ)
        server = self._serve()
        try:
            client = HttpAgentClient(
                f"http://127.0.0.1:{server.server_port}", "m",
                api_key_env="HIPPO_PROBE_DIRECT_KEY", timeout=10,
                env={"HIPPO_PROBE_DIRECT_KEY": "sk-injected"},
            )
            client.run("x")
            self.assertEqual(server.captured["auth"], "Bearer sk-injected")  # type: ignore[attr-defined]
        finally:
            server.shutdown()

    def test_env_override_excludes_process_environ(self):
        # B1：一旦注入 env，就不得回頭從 os.environ 借 key（互動 shell 滲漏）
        server = self._serve()
        try:
            with mock.patch.dict("os.environ", {"HIPPO_TEST_KEY": "sk-local"}):
                client = HttpAgentClient(
                    f"http://127.0.0.1:{server.server_port}", "m",
                    api_key_env="HIPPO_TEST_KEY", timeout=10, env={},
                )
                client.run("x")
            self.assertIsNone(server.captured["auth"])  # type: ignore[attr-defined]
        finally:
            server.shutdown()


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
        # migration 後 doctor 會對 exe 做 smoke probe：需 exit 0＋非空回應
        exe.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
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
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value={"PATH": "/usr/bin:/bin"}), \
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
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value={"PATH": "/usr/bin:/bin"}), \
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
                 mock.patch.object(ops, "_service_manager_environment",
                                   return_value={"PATH": "/usr/bin:/bin"}), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                self.assertEqual(ops.run_doctor(fix_backend=True), 1)
