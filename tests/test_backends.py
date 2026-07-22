"""Backend preset registry（PR-D 契約 7）單元測試。"""
from __future__ import annotations

import os
import stat
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import backends


class RegistryContractTests(unittest.TestCase):
    def test_presets_expose_contract_names(self):
        for expected in (
            "claude-headless", "codex-headless", "copilot-headless",
            "gemini-headless", "antigravity-headless",
            "agy-headless", "cg-headless", "co-gem-headless",
            "claude-gem-headless", "custom-argv",
        ):
            self.assertIn(expected, backends.PRESETS)

    def test_default_preset_is_first_and_claude(self):
        self.assertEqual(next(iter(backends.PRESETS)), "claude-headless")

    def test_preset_dataclass_is_frozen(self):
        preset = backends.PRESETS["claude-headless"]
        with self.assertRaises(FrozenInstanceError):
            preset.name = "x"

    def test_names_match_keys(self):
        for key, preset in backends.PRESETS.items():
            self.assertEqual(key, preset.name)

    def test_unverified_presets_marked_unavailable(self):
        """gemini：僅 rc=41（auth 未備）觀察、無成功 round-trip 實證——依 spec
        §8「不猜 argv」標 unavailable；antigravity：命令契約未確認（spec §2）。"""
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertFalse(preset.available)
                self.assertEqual(preset.argv_template, [])

    def test_argv_presets_use_stdin_mechanism(self):
        for name in ("claude-headless", "codex-headless", "copilot-headless"):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertTrue(preset.available)
                self.assertIn("argv-stdin", preset.capabilities)
                self.assertTrue(preset.argv_template)
                self.assertEqual(preset.argv_template[0], preset.required_executable)
                self.assertEqual(
                    preset.doctor_probe, [preset.required_executable, "--version"])

    def test_verified_argv_templates(self):
        """2026-07-10 本機實測定案的 argv（見 plan「本機實測基線」）。"""
        self.assertEqual(backends.PRESETS["claude-headless"].argv_template,
                         ["claude", "-p"])
        self.assertEqual(
            backends.PRESETS["codex-headless"].argv_template,
            ["codex", "exec", "--skip-git-repo-check",
             "--sandbox", "read-only", "--color", "never", "-"])
        # copilot：實測帶非空 -p 時 stdin 注入不可靠——stdin 必須是唯一 prompt 來源
        self.assertEqual(backends.PRESETS["copilot-headless"].argv_template,
                         ["copilot", "-s", "--no-color"])
        # gemini-headless：候選 argv 未經 round-trip 實證，不入 registry
        # template（unavailable；升級前提見 docs/backend-matrix.md）。

    def test_custom_preset_has_no_argv_template(self):
        for name in ("custom-argv",):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertEqual(preset.argv_template, [])
                self.assertIsNone(preset.required_executable)
                self.assertIsNone(preset.doctor_probe)
        self.assertIn("user-defined", backends.PRESETS["custom-argv"].capabilities)


class ProbeTests(unittest.TestCase):
    def _make_bin(self, dir_path: Path, name: str, script: str) -> Path:
        path = dir_path / name
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def test_service_effective_env_shape(self):
        env = backends.service_effective_env()
        self.assertEqual(env["PATH"], backends.SERVICE_EFFECTIVE_PATH)
        self.assertNotIn("nvm", env["PATH"])
        self.assertTrue(env["HOME"])

    def test_probe_ok_with_stub_executable(self):
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex", "#!/bin/sh\necho fake-codex 9.9\nexit 0\n")
            result = backends.probe_preset(
                backends.PRESETS["codex-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp}, live=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.executable, str(bin_dir / "codex"))
        self.assertIn("fake-codex 9.9", result.detail)

    def test_probe_failure_reports_rc_and_stderr(self):
        # rc=41 情境取材自 gemini 實測；gemini-headless 已標 unavailable（probe
        # 宣告層短路），故以 available 的 copilot-headless stub 驗證 rc/stderr 回報。
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "copilot", "#!/bin/sh\necho auth broken >&2\nexit 41\n")
            result = backends.probe_preset(
                backends.PRESETS["copilot-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp}, live=True)
        self.assertFalse(result.ok)
        self.assertIn("rc=41", result.detail)
        self.assertIn("auth broken", result.detail)

    def test_probe_missing_executable(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PATH": tmp}):
                result = backends.probe_preset(
                    backends.PRESETS["copilot-headless"],
                    env={"PATH": tmp, "HOME": tmp})
        self.assertFalse(result.ok)
        self.assertIsNone(result.executable)
        self.assertIn("未安裝", result.detail)

    def test_probe_notes_user_only_path_visibility(self):
        with TemporaryDirectory() as tmp:
            user_bin = Path(tmp) / "user-bin"
            user_bin.mkdir()
            self._make_bin(user_bin, "claude", "#!/bin/sh\necho 9.9 fake\nexit 0\n")
            service_bin = Path(tmp) / "svc-bin"
            service_bin.mkdir()
            with mock.patch.dict(os.environ, {"PATH": f"{user_bin}:/usr/bin:/bin"}):
                result = backends.probe_preset(
                    backends.PRESETS["claude-headless"],
                    env={"PATH": f"{service_bin}:/usr/bin:/bin", "HOME": tmp})
        self.assertTrue(result.ok)
        self.assertIn("不在 service PATH", result.detail)

    def test_probe_unavailable_and_config_driven_short_circuit(self):
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(preset=name):
                result = backends.probe_preset(backends.PRESETS[name])
                self.assertFalse(result.available)
                self.assertIsNone(result.ok)
                self.assertIn("unavailable", result.detail)
        custom = backends.probe_preset(backends.PRESETS["custom-argv"])
        self.assertTrue(custom.available)
        self.assertIsNone(custom.ok)
        self.assertIn("config 驅動", custom.detail)

    def test_probe_timeout_is_failure_not_crash(self):
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex", "#!/bin/sh\nsleep 30\n")
            result = backends.probe_preset(
                backends.PRESETS["codex-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp},
                timeout=1, live=True)
        self.assertFalse(result.ok)
        self.assertIn("probe 失敗", result.detail)

    def test_probe_not_live_is_resolution_only(self):
        # 裸 doctor 預設 live=False：解析得到即回 ok=True 的解析級結果，完全不 exec
        # doctor_probe（不喚起 backend）——stub 的輸出不得出現在 detail，且
        # subprocess.run 若被呼叫即判測試失敗（鎖住 opt-in 閘門）。
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex",
                           "#!/bin/sh\necho SHOULD-NOT-RUN 9.9\nexit 0\n")
            with mock.patch.object(
                    backends.subprocess, "run",
                    side_effect=AssertionError("live=False 不得 exec doctor_probe")):
                result = backends.probe_preset(
                    backends.PRESETS["codex-headless"],
                    env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp})
        self.assertTrue(result.ok)
        self.assertEqual(result.executable, str(bin_dir / "codex"))
        self.assertNotIn("SHOULD-NOT-RUN", result.detail)
        self.assertIn("即時 probe", result.detail)

    def test_probe_non_utf8_output_is_failure_not_crash(self):
        # blocking 回歸：doctor_probe 目標吐非 UTF-8 位元組（跑錯 binary／crash
        # dump／locale 錯亂）時，text=True 的 decode 於 subprocess.run() 內拋
        # UnicodeDecodeError（ValueError 子類，非 OSError）。probe_preset 必須攔下
        # 判 FAIL、不得逸出——否則 run_doctor 逐 preset 呼叫無 try/except 包覆，
        # 例外一路傳到 cli.main，整支 `hippo doctor` 崩潰、rc!=0（違反 Task 4
        # 「preset 矩陣只報告、不影響 exit code」契約）。live=True 才走 exec 分支；
        # stub 即使 exit 0，decode 也在檢查 returncode 前就拋。
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex",
                           '#!/bin/sh\nprintf "\\377\\376\\200\\201"\nexit 0\n')
            result = backends.probe_preset(
                backends.PRESETS["codex-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp}, live=True)
        self.assertEqual(result.preset, "codex-headless")
        self.assertFalse(result.ok)
        self.assertIn("非 UTF-8", result.detail)


if __name__ == "__main__":
    unittest.main()
