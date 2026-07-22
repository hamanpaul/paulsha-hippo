"""第二刀元件測試：init/doctor/install service/supervise 與蒸餾 backend。"""
from __future__ import annotations

import io
import multiprocessing
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from paulsha_hippo import ops, paths


def _concurrent_init_worker(env, fail_second_replace, result_path):
    """fork 子進程 entrypoint：設 env、可注入第二次 os.replace 失敗，跑 run_init。

    兩個此 worker 並行驗證 init transaction lock（#15 Codex high）：第二次 commit
    失敗的交易 rollback 不得誤刪另一交易寫入的有效 config、不留 config/override
    不一致。結果（`ok:<rc>` 或 `err:<type>`）寫入 result_path 供父進程觀察。"""
    import os as _os
    from unittest import mock as _mock

    from paulsha_hippo import ops as _ops

    _os.environ.update(env)
    real_replace = _os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if fail_second_replace and calls["n"] == 2:
            raise OSError("injected second-replace failure (concurrent init)")
        return real_replace(src, dst)

    try:
        with _mock.patch.object(_ops.shutil, "which", return_value="/fake/bin/claude"), \
             _mock.patch.object(_ops.os, "replace", side_effect=flaky):
            rc = _ops.run_init(
                memory_root=None, backend="claude-headless",
                base_url=None, api_key_env=None, model=None, assume_yes=True,
            )
        outcome = f"ok:{rc}"
    except BaseException as exc:  # noqa: BLE001 — 交易失敗屬預期，記錄供父進程斷言
        outcome = f"err:{type(exc).__name__}"
    with open(result_path, "w", encoding="utf-8") as fh:
        fh.write(outcome)


class InitConcurrencyTests(unittest.TestCase):
    """#15 Codex high：init transaction 併發／rollback 危害硬化。

    存在性檢查→stage→commit→rollback 全程受同一把 fcntl.flock 交易鎖保護，且
    rollback 只移除「可證明由本交易建立、且自建立後未被替換」的檔案——第二次
    commit 失敗時不得盲刪已被並行 writer 替換的有效 config、不留 config/override
    不一致。"""

    def _env(self, tmp):
        return {
            "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
            "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
            "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
        }

    def _paths(self, tmp):
        cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
        override = (Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                    / "atomizer.override.yaml")
        return cfg, override

    def test_rollback_spares_config_replaced_by_concurrent_writer(self):
        # 決定性回歸（抓誤刪）：本交易 commit config 後、第二次 commit（override）失敗前，
        # 模擬並行交易以自己的有效 config 替換同一路徑（inode 改變）。盲刪版 rollback 會
        # unlink 該路徑而誤刪對方有效 config；硬化後 rollback 以 inode 佐證只移除本交易
        # 所寫且未被替換者，對方 config 應完好保留。
        with TemporaryDirectory() as tmp:
            cfg, override = self._paths(tmp)
            foreign = ("memory_root: /foreign/valid\n"
                       "distiller:\n  backend: custom-argv\n")
            real_replace = os.replace
            calls = {"n": 0}

            def orchestrated_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    # 我方 config 已 commit 且被指紋化；此刻並行交易以有效 config 替換之
                    cfg.parent.mkdir(parents=True, exist_ok=True)
                    ftmp = cfg.parent / ".foreign.tmp"
                    ftmp.write_text(foreign, encoding="utf-8")
                    real_replace(str(ftmp), str(cfg))
                    raise OSError("override commit fails after concurrent replace")
                return real_replace(src, dst)

            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops.shutil, "which", return_value="/fake/bin/claude"), \
                 mock.patch.object(ops.os, "replace", side_effect=orchestrated_replace):
                with self.assertRaises(OSError):
                    ops.run_init(
                        memory_root=None, backend="claude-headless",
                        base_url=None, api_key_env=None, model=None, assume_yes=True,
                    )
            # 併發交易寫入的有效 config 未被本交易 rollback 誤刪
            self.assertTrue(cfg.exists(), "並行交易寫入的有效 config 不得被 rollback 誤刪")
            self.assertEqual(cfg.read_text(encoding="utf-8"), foreign)
            # 不殘留暫存檔（override 端本交易的 staged tmp 由 finally 清掉）
            self.assertEqual(list(cfg.parent.glob("*.tmp")), [])
            if override.parent.exists():
                self.assertEqual(list(override.parent.glob("*.tmp")), [])

    def test_concurrent_init_second_commit_failure_keeps_valid_config(self):
        # 併發兩個 init（fork）：A 注入第二次 commit 失敗 → 其交易 rollback；B 正常寫入。
        # transaction lock 序列化兩者，最終狀態必一致：有效 config 存在且與 override
        # 一致，A 的 rollback 不得刪掉 B 寫入的有效 config、不留半初始化。
        ctx = multiprocessing.get_context("fork")
        with TemporaryDirectory() as tmp:
            env = self._env(tmp)
            cfg, override = self._paths(tmp)
            ra = Path(tmp) / "result_a.txt"
            rb = Path(tmp) / "result_b.txt"
            pa = ctx.Process(target=_concurrent_init_worker, args=(env, True, str(ra)))
            pb = ctx.Process(target=_concurrent_init_worker, args=(env, False, str(rb)))
            pa.start()
            pb.start()
            pa.join(30)
            pb.join(30)
            self.assertFalse(pa.is_alive(), "worker A 逾時未結束")
            self.assertFalse(pb.is_alive(), "worker B 逾時未結束")
            # 不變量：有效 config 存在（B 寫入），未被 A 的 rollback 誤刪
            self.assertTrue(cfg.exists(), "有效 config 不得被並行交易 rollback 誤刪")
            self.assertIn("selected_profile: claude-headless", cfg.read_text(encoding="utf-8"))
            # 一致性：config 與 override 皆存在（B 成功寫入兩者），無「config 有／override 缺」
            self.assertTrue(override.exists(), "不得留 config/override 不一致（override 缺）")
            # PR-D Task 3：argv token 改以 json.dumps 加引號輸出（路徑含空白也安全），
            # 以 yaml.safe_load 解析後比對結構，語意不變、不做未加引號的 substring 斷言。
            import yaml
            override_data = yaml.safe_load(override.read_text(encoding="utf-8"))
            self.assertEqual(override_data["agent_exec"]["command"][0], "/fake/bin/claude")
            # 不殘留暫存檔
            self.assertEqual(list(cfg.parent.glob("*.tmp")), [])
            self.assertEqual(
                list(override.parent.glob("*.tmp")) if override.parent.exists() else [],
                [],
            )


class InitTests(unittest.TestCase):
    def test_init_claude_headless_writes_config_and_override(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=lambda argv: ["/fake/abs/claude"] + list(argv[1:])):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 0)
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertIn("selected_profile: claude-headless", cfg.read_text(encoding="utf-8"))
            import yaml
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            data = yaml.safe_load(override.read_text(encoding="utf-8"))
            self.assertEqual(str(data["schema_version"]), "1")
            self.assertEqual(data["agent_exec"]["command"], ["/fake/abs/claude", "-p"])

    def test_init_each_argv_preset_writes_registry_template(self):
        from paulsha_hippo import backends
        for name in ("claude-headless", "codex-headless", "copilot-headless"):
            with self.subTest(backend=name), TemporaryDirectory() as tmp:
                env = {
                    "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                    "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                    "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
                }
                template = backends.PRESETS[name].argv_template
                with mock.patch.dict("os.environ", env), \
                     mock.patch.object(ops, "resolve_backend_argv",
                                       side_effect=lambda argv: ["/fake/abs/" + argv[0]] + list(argv[1:])):
                    rc = ops.run_init(memory_root=None, backend=name, base_url=None,
                                      api_key_env=None, model=None, assume_yes=True)
                self.assertEqual(rc, 0)
                import yaml
                data = yaml.safe_load(
                    (Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                     / "atomizer.override.yaml").read_text(encoding="utf-8"))
                self.assertEqual(
                    data["agent_exec"]["command"],
                    ["/fake/abs/" + template[0]] + list(template[1:]))

    def test_init_argv_preset_includes_model_when_given(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=lambda argv: ["/fake/abs/codex"] + list(argv[1:])):
                rc = ops.run_init(memory_root=None, backend="codex-headless",
                                  base_url=None, api_key_env=None,
                                  model="gpt-5.4", assume_yes=True)
            self.assertEqual(rc, 0)
            import yaml
            data = yaml.safe_load(
                (Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                 / "atomizer.override.yaml").read_text(encoding="utf-8"))
            self.assertEqual(data["agent_exec"]["model"], "gpt-5.4")

    def test_init_argv_preset_backend_unavailable_fails_closed(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=ops.BackendUnavailableError("codex not found")):
                rc = ops.run_init(memory_root=None, backend="codex-headless",
                                  base_url=None, api_key_env=None, model=None, assume_yes=True)
            self.assertEqual(rc, 2)
            self.assertFalse((Path(tmp) / "hippo-cfg" / "config.yaml").exists())
            self.assertFalse((Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                              / "atomizer.override.yaml").exists())

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
            # blocking：驗證失敗前不得寫入任一設定檔——config 與 override 皆不存在，
            # 否則會留下「宣告 claude-headless 卻缺 override」的半初始化不一致設定。
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            self.assertFalse(override.exists())
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertFalse(cfg.exists())

    def test_init_retired_provider_backend_writes_no_files(self):
        # direct provider backend is retired; validation must fail before any file write.
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env):
                rc = ops.run_init(
                    memory_root=None, backend="http",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 2)
            self.assertFalse((Path(tmp) / "hippo-cfg" / "config.yaml").exists())
            self.assertFalse(
                (Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml").exists()
            )

    def test_init_backend_missing_keeps_existing_config_unmodified(self):
        # 既有 config 在驗證失敗時必須維持原內容，不得被半初始化寫入污染。
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("memory_root: /keep/me\ndistiller:\n  backend: custom-argv\n",
                           encoding="utf-8")
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 2)
            self.assertEqual(cfg.read_text(encoding="utf-8"),
                             "memory_root: /keep/me\ndistiller:\n  backend: custom-argv\n")

    def test_init_claude_headless_commits_config_and_override_atomically(self):
        # 驗證全過才落地：config 與 override 都寫入，且不殘留暫存檔（.tmp）。
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
            cfg_dir = Path(tmp) / "hippo-cfg"
            self.assertTrue((cfg_dir / "config.yaml").is_file())
            override_dir = Path(tmp) / "legacy" / ".config" / "paulshaclaw"
            self.assertTrue((override_dir / "atomizer.override.yaml").is_file())
            leftover = list(cfg_dir.glob("*.tmp")) + list(override_dir.glob("*.tmp"))
            self.assertEqual(leftover, [])

    def test_init_rolls_back_first_file_when_second_commit_fails(self):
        # 硬化：驗證全過但 commit 第二個檔（override）時 IO 失敗——第一個已 commit 的
        # 新檔（config）必須回復到不存在，且不殘留暫存檔，杜絕半初始化殘留。
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            real_replace = os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("disk gone")
                return real_replace(src, dst)

            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value="/fake/bin/claude"), \
                 mock.patch.object(ops.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    ops.run_init(
                        memory_root=None, backend="claude-headless",
                        base_url=None, api_key_env=None, model=None, assume_yes=True,
                    )
            cfg_dir = Path(tmp) / "hippo-cfg"
            override_dir = Path(tmp) / "legacy" / ".config" / "paulshaclaw"
            self.assertFalse((cfg_dir / "config.yaml").exists())
            self.assertFalse((override_dir / "atomizer.override.yaml").exists())
            leftover = (list(cfg_dir.glob("*.tmp")) if cfg_dir.exists() else []) + \
                       (list(override_dir.glob("*.tmp")) if override_dir.exists() else [])
            self.assertEqual(leftover, [])

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

    def test_doctor_reports_backend_preset_matrix(self):
        from paulsha_hippo import backends

        def fake_probe(preset, *, env=None, timeout=30, live=False):
            if preset.name == "claude-headless":
                return backends.ProbeResult(preset.name, True, "/abs/claude", True,
                                            "2.1.206 (Claude Code)")
            if preset.name == "codex-headless":
                return backends.ProbeResult(preset.name, True, "/abs/codex", False,
                                            "probe rc=127：node not found")
            if not preset.available:
                return backends.ProbeResult(preset.name, False, None, None,
                                            "unavailable（命令契約未確認，選單不可選）")
            if preset.required_executable is not None:
                return backends.ProbeResult(preset.name, True, None, False,
                                            "executable 未安裝")
            return backends.ProbeResult(preset.name, True, None, None,
                                        "config 驅動（無本機執行檔需求）")

        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}
        buf = io.StringIO()
        # 比照本 class 既有測試的 _PROBE_OK 手法：patch 掉 PR-A 的
        # _probe_backend_service_effective，隔離 configured-backend probe 的環境依賴
        # （否則 CI 無 backend → probe FAIL → rc=1，下方 rc==0 斷言必紅）。
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=("- distiller backend：✓ mocked", False)), \
             mock.patch.object(ops.backends, "probe_preset", side_effect=fake_probe), \
             redirect_stdout(buf):
            rc = ops.run_doctor()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("- backend presets（service-effective probe）:", out)
        self.assertIn("  - claude-headless: ✓ /abs/claude（2.1.206 (Claude Code)）", out)
        self.assertIn("  - codex-headless: ✗ probe rc=127：node not found", out)
        self.assertIn("  - copilot-headless: ✗ executable 未安裝", out)
        self.assertIn("  - gemini-headless: ✗ unavailable（命令契約未確認，選單不可選）", out)
        self.assertIn("  - antigravity-headless: ✗ unavailable（命令契約未確認，選單不可選）", out)
        self.assertIn("  - custom-argv: - config 驅動（無本機執行檔需求）", out)


class DoctorBackendProbeTests(unittest.TestCase):
    """live probe（smoke exec／HTTP）語意——一律經 opt-in gate（live_probe=True）驅動。

    裸 `hippo doctor` 預設不進本 class 驗的 exec 分支（見 DoctorLiveProbeGateTests）；
    唯一例外是解析級 gate 本身（bare command 解析不到照樣 FAIL）。"""

    # HIPPO_DOCTOR_LIVE_PROBE 置空：中和開發機環境殘留，維持測試決定性
    _ENV = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a",
            "HIPPO_DOCTOR_LIVE_PROBE": ""}

    def _fake_cfg(self, **overrides):
        base = dict(
            agent_exec_backend="custom-argv",
            agent_exec_command=("claude", "-p"),
            agent_exec_model="test-model",
            default_promoter="llm",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_probe_fails_when_bare_command_unresolvable_in_service_path(self):
        # 刻意用裸 run_doctor()：解析級 gate（shutil.which）不需 live opt-in 也要 FAIL
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
                self.assertEqual(ops.run_doctor(live_probe=True), 0)

    def test_service_effective_path_falls_back_without_systemd(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=OSError("no systemctl")):
            self.assertEqual(ops._service_effective_path_env(), "/usr/local/bin:/usr/bin:/bin")

    def _doctor_with_command(self, command: tuple[str, ...], *, live: bool = True) -> int:
        cfg = self._fake_cfg(agent_exec_command=command)
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin"}):
            return ops.run_doctor(live_probe=live)

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

    def test_probe_fails_on_non_utf8_output(self):
        # text=True 以 UTF-8 解 stdout/stderr；backend 吐非 UTF-8 位元組（跑錯
        # binary／crash dump／locale 錯亂的錯誤文字）時，decode 於 run() 內即拋
        # UnicodeDecodeError（ValueError 子類、非 OSError）。此正是本 probe 要偵測
        # 的故障類——須 fail-closed 判 FAIL，不得讓 UnicodeDecodeError 逸出崩潰 CLI
        # （run_doctor→_ops_doctor→cli.main 皆不接此例外，否則 assertEqual 會被
        # traceback 取代而 error）。stdout 非 UTF-8＋exit 0：decode 於檢查 exit 前就拋。
        self.assertEqual(
            self._doctor_with_command(("/bin/sh", "-c", r'printf "\377\376\200\201"')),
            1,
        )
        # stderr 非 UTF-8（text=True 同時解 stderr）同樣不得崩潰、判 FAIL。
        self.assertEqual(
            self._doctor_with_command(("/bin/sh", "-c", r'printf "\377\376\200\201" >&2')),
            1,
        )

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
            return ops.run_doctor(live_probe=True)

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

    def test_probe_rejects_api_key_only_in_manager_env(self):
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
                1,
            )

    def test_probe_fallback_marks_approximate_when_no_user_bus(self):
        # 無 systemd user bus（CI 等）→ fallback 現行近似（os.environ + 保守 PATH），
        # 且輸出必須明確標示「近似，非 service-effective」。
        with TemporaryDirectory() as tmp:
            exe = self._key_gated_backend(tmp, "HIPPO_PROBE_FAKE_KEY")
            buf = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = self._doctor_with_key_gated_backend(
                    exe,
                    shell_env={"HIPPO_PROBE_FAKE_KEY": "sk-shell-only"},
                    manager_env=None,
                )
            self.assertEqual(rc, 1)
            self.assertIn("近似", err.getvalue())
            self.assertIn("非 service-effective", err.getvalue())


class DoctorLiveProbeGateTests(unittest.TestCase):
    """blocking review 修正：裸 `hippo doctor` 必須是快速、免費、無副作用的解析級健檢。

    live smoke probe（真實喚起 backend／真 HTTP）僅在 opt-in gate 開啟時執行：
    `--fix-backend`／`--probe-live`（`run_doctor(live_probe=True)`）／
    `HIPPO_DOCTOR_LIVE_PROBE=1`。跨批次（PR-C/PR-D）直呼 run_doctor 的測試
    在預設 gate 關閉下不會打 LLM。"""

    _ENV = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a",
            "HIPPO_DOCTOR_LIVE_PROBE": ""}
    # /usr/bin/env 解析檢查全過（存在＋X_OK），但實跑 exit 127——
    # 專門用來區分「解析級（過）」與「live smoke exec（fail-closed 不過）」兩檔。
    _RESOLVES_BUT_FAILS_LIVE = ("/usr/bin/env", "definitely-no-such-runtime")

    def _fake_cfg(self, **overrides):
        base = dict(
            agent_exec_backend="custom-argv",
            agent_exec_command=self._RESOLVES_BUT_FAILS_LIVE,
            agent_exec_model="test-model",
            default_promoter="llm",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _doctor(self, cfg, *, extra_env: dict[str, str] | None = None,
                **doctor_kwargs) -> int:
        with mock.patch.dict("os.environ", {**self._ENV, **(extra_env or {})}), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin"}):
            return ops.run_doctor(**doctor_kwargs)

    def test_bare_doctor_resolution_only_never_execs_backend(self):
        # 裸 doctor：解析檢查過即 PASS，且完全不 exec backend（無 LLM 成本／延遲）
        with mock.patch.object(
                ops, "_exec_probe_service_effective",
                side_effect=AssertionError("裸 doctor 不得真實 exec backend")) as spy:
            self.assertEqual(self._doctor(self._fake_cfg()), 0)
        spy.assert_not_called()

    def test_live_probe_flag_upgrades_to_smoke_exec(self):
        # 同一 config：gate 開啟才走 fail-closed smoke exec（exit 127 → FAIL）
        self.assertEqual(self._doctor(self._fake_cfg(), live_probe=True), 1)

    def test_env_var_upgrades_bare_doctor_to_live(self):
        self.assertEqual(
            self._doctor(self._fake_cfg(),
                         extra_env={"HIPPO_DOCTOR_LIVE_PROBE": "1"}),
            1,
        )

    def test_fix_backend_implies_live_probe(self):
        # spec §4.1 恢復序列 gate：--fix-backend 明確要求「實際喚起 backend 一次」。
        # config roots 指向空 tmp → migration 無 override 可遷移（noop），
        # 隨後的 doctor 檢查必須以 live 檔執行 → exit 127 FAIL。
        with TemporaryDirectory() as tmp:
            rc = self._doctor(
                self._fake_cfg(),
                extra_env={"HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                           "PSC_CONFIG_ROOT": f"{tmp}/legacy-cfg"},
                fix_backend=True,
            )
        self.assertEqual(rc, 1)

    def test_bare_doctor_fails_when_absolute_backend_not_executable(self):
        # 解析級 gate 仍要抓得住壞 backend：is_file+X_OK 不過 → FAIL
        with TemporaryDirectory() as tmp:
            not_exec = Path(tmp) / "claude"
            not_exec.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            not_exec.chmod(0o644)  # 無執行權限
            cfg = self._fake_cfg(agent_exec_command=(str(not_exec), "-p"))
            self.assertEqual(self._doctor(cfg), 1)

    def test_gate_wiring_passes_live_kwarg_to_probe(self):
        # 跨批次契約（PR-C/PR-D mock 面）：run_doctor 以 live= kwarg 驅動 probe
        for doctor_kwargs, expected_live in (
            ({}, False),
            ({"live_probe": True}, True),
        ):
            with self.subTest(doctor_kwargs=doctor_kwargs):
                with mock.patch.dict("os.environ", self._ENV), \
                     mock.patch.object(
                         ops, "_probe_backend_service_effective",
                         return_value=("- distiller backend：✓ mocked", False)) as probe:
                    self.assertEqual(ops.run_doctor(**doctor_kwargs), 0)
                probe.assert_called_once_with(live=expected_live)

    @staticmethod
    def _stub_preset_bins(tmp: str) -> dict[str, str]:
        # 三家 argv preset 執行檔的 stub（內容無關——resolution 只需存在＋X_OK，
        # 且 live 分支下 subprocess.run 已被 spy 攔截、stub 不會真跑）；回傳可餵給
        # backends.service_effective_env 的 env（PATH 命中 tmpdir，模擬本機裝有
        # claude/codex/copilot 三家 CLI）。
        bin_dir = Path(tmp)
        for name in ("claude", "codex", "copilot"):
            exe = bin_dir / name
            exe.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
            exe.chmod(0o755)
        return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp}

    def test_bare_doctor_preset_matrix_never_execs_subprocess(self):
        # blocking 回歸：裸 doctor 的 preset 矩陣必須解析級——即使每個 preset 執行檔
        # 都解析得到（stub 命中 service_effective_env，模擬同時裝有 claude/codex/
        # copilot 的開發機，正是本 blocking 的重現環境），也不得對任何 preset 觸發
        # 真實 `<exe> --version` 子程序（無 backend 喚起／潛在網路存取）。CI 因 CLI
        # 未安裝、which 落空而 subprocess 分支走不到，故此處強制解析命中，鎖住既有
        # 測試套件測不到的路徑。
        # 註：backends.subprocess 與 ops.subprocess 是同一 module 物件，故一併把
        # _systemd_user_available 收斂為 False，避免 systemctl 探測誤觸 spy。
        with TemporaryDirectory() as tmp:
            fake_env = self._stub_preset_bins(tmp)
            with mock.patch.object(ops, "_systemd_user_available", return_value=False), \
                 mock.patch.object(ops.backends, "service_effective_env",
                                   return_value=fake_env), \
                 mock.patch.object(
                     ops.backends.subprocess, "run",
                     side_effect=AssertionError(
                         "裸 doctor 不得對 preset 觸發真實 subprocess exec")) as spy:
                self.assertEqual(self._doctor(self._fake_cfg()), 0)
            spy.assert_not_called()

    def test_live_probe_preset_matrix_execs_resolved_presets(self):
        # 對照組：gate 開啟時，preset 矩陣確實對解析得到的 preset exec
        # `<exe> --version`——證明上面的 no-exec 斷言非因解析落空而 vacuous，且
        # preset 矩陣與 configured-backend probe 共用同一 opt-in 閘門。
        completed = SimpleNamespace(returncode=0, stdout="1.0 fake", stderr="")
        with TemporaryDirectory() as tmp:
            fake_env = self._stub_preset_bins(tmp)
            with mock.patch.object(ops, "_systemd_user_available", return_value=False), \
                 mock.patch.object(ops.backends, "service_effective_env",
                                   return_value=fake_env), \
                 mock.patch.object(ops.backends.subprocess, "run",
                                   return_value=completed) as spy, \
                 mock.patch.object(ops, "_probe_backend_service_effective",
                                   return_value=("- distiller backend：✓ mocked", False)):
                self.assertEqual(
                    self._doctor(self._fake_cfg(), live_probe=True), 0)
            self.assertTrue(spy.called)


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
        # B1 核心：service-effective 模式只取 manager 的 PATH；其餘子程序
        # 環境由固定 minimal non-secret allowlist 建立，credential 不得滲入。
        with mock.patch.dict("os.environ", {"HIPPO_SHELL_ONLY_VAR": "1"}), \
             mock.patch.object(ops, "_service_manager_environment",
                               return_value={"PATH": "/usr/bin:/bin", "HOME": "/home/u"}):
            env, service_effective = ops._probe_environment()
        self.assertTrue(service_effective)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertNotEqual(env["HOME"], "/home/u")
        self.assertNotIn("HIPPO_SHELL_ONLY_VAR", env)
        self.assertIn("HIPPO_SELF_SESSION", env)

    def test_manager_env_without_path_gets_conservative_default(self):
        with mock.patch.object(ops, "_service_manager_environment",
                               return_value={"HOME": "/home/u"}):
            env, service_effective = ops._probe_environment()
        self.assertTrue(service_effective)
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertNotEqual(env["HOME"], "/home/u")

    def test_fallback_mode_keeps_only_minimal_approximation(self):
        with mock.patch.dict("os.environ", {"HIPPO_SHELL_ONLY_VAR": "1"}), \
             mock.patch.object(ops, "_service_manager_environment", return_value=None):
            env, service_effective = ops._probe_environment()
        self.assertFalse(service_effective)
        self.assertNotIn("HIPPO_SHELL_ONLY_VAR", env)
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


class BackendConfigTests(unittest.TestCase):
    def test_direct_provider_backend_is_retired(self):
        from paulsha_hippo.atomizer import config as aconfig

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "atomizer.yaml"
            base = (Path(aconfig.DEFAULT_CONFIG_DIR) / "atomizer.yaml").read_text(encoding="utf-8")
            cfg.write_text(base + "\n", encoding="utf-8")
            override = Path(tmp) / "override.yaml"
            override.write_text(
                'schema_version: "1"\nagent_exec:\n  backend: http\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(aconfig.AtomizerConfigError, "operator-redaction"):
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
        self.assertIn("--python", argv)
        self.assertEqual(argv[argv.index("--python") + 1], sys.executable)
        run.assert_called_once()


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


class BackendRegistryWiringTests(unittest.TestCase):
    def test_backends_tuple_derived_from_registry(self):
        from paulsha_hippo import backends
        self.assertEqual(ops._BACKENDS, tuple(backends.PRESETS))

    def test_init_rejects_unknown_backend(self):
        rc = ops.run_init(memory_root=None, backend="definitely-not-a-backend",
                          base_url=None, api_key_env=None, model=None, assume_yes=True)
        self.assertEqual(rc, 2)

    def test_init_rejects_unavailable_presets(self):
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(backend=name):
                rc = ops.run_init(memory_root=None, backend=name, base_url=None,
                                  api_key_env=None, model=None, assume_yes=True)
                self.assertEqual(rc, 2)


class InitBackendChoicesTests(unittest.TestCase):
    def test_parser_accepts_all_registry_presets(self):
        from paulsha_hippo import backends
        from paulsha_hippo.cli import _build_parser
        parser = _build_parser()
        for name in backends.PRESETS:
            with self.subTest(backend=name):
                args = parser.parse_args(["init", "--backend", name])
                self.assertEqual(args.backend, name)

    def test_parser_rejects_non_registry_backend(self):
        from paulsha_hippo.cli import _build_parser
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["init", "--backend", "definitely-not-a-backend"])


class SuperviseCliWiringTests(unittest.TestCase):
    def test_supervise_cli_forwards_once_and_overrides(self):
        from paulsha_hippo import cli as memory_cli
        captured: dict = {}

        def fake_supervise(*, interval, extra_argv=None, once=False, runner=None):
            captured.update(interval=interval, extra_argv=list(extra_argv or []), once=once)
            return 0

        with mock.patch.object(ops, "run_dream_supervise", side_effect=fake_supervise):
            rc = memory_cli.main([
                "dream", "supervise", "--interval", "5", "--once",
                "--memory-root", "/mr", "--max-load", "99.5",
                "--promoter", "identity", "--agent-command", "python x.py",
            ])
        self.assertEqual(rc, 0)
        self.assertTrue(captured["once"])
        self.assertEqual(captured["interval"], 5)
        self.assertEqual(captured["extra_argv"], [
            "--memory-root", "/mr", "--max-load", "99.5",
            "--promoter", "identity", "--agent-command", "python x.py"])
