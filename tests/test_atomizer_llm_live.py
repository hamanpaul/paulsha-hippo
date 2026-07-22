from __future__ import annotations

import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import backends, cli, paths
from paulsha_hippo.atomizer.agent_exec import AgentExecClient, AgentExecError
from paulsha_hippo.lib.lifecycle.gate import run_static_gate_check_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"

_LIVE_TIMEOUT_SECONDS = 300

_MATRIX_PRESETS = (
    "claude-headless", "codex-headless", "agy-headless",
)


class SmokeMatrixCoverageTests(unittest.TestCase):
    """升級防護（docs/backend-matrix.md）：available 的 argv preset 必在 smoke 矩陣。

    unavailable preset（如 gemini-headless）翻 available=True 時本測試 FAIL，
    強制同 PR 補對應 live smoke——「round-trip 實證→翻 available→補 smoke」
    的機械 gate。無 env gate，一般 CI 常跑。
    """

    def test_matrix_covers_every_available_argv_preset(self):
        available_argv = tuple(
            name for name, preset in backends.PRESETS.items()
            if preset.available
            and "argv-stdin" in preset.capabilities
            and "user-defined" not in preset.capabilities
        )
        self.assertEqual(available_argv, _MATRIX_PRESETS)


@unittest.skipUnless(
    os.environ.get("PSC_ATOMIZE_LIVE"),
    "set PSC_ATOMIZE_LIVE=1 to enable real-backend distillation smokes",
)
class AtomizerLlmLiveMatrixTests(unittest.TestCase):
    """spec §3.5.5 真蒸餾 smoke：同一 fixture session × 每個 available argv preset。

    happy path（純 JSON 情境）一輪；其餘四情境見 mock 矩陣
    （tests/test_atomizer_backend_matrix.py）。未安裝或 probe 失敗（auth／
    配額／PATH 故障）→ skip 並回報原因——不擋批次，但列入 #10 缺項
    （spec §3.5 關單條件、§8 風險表）。registry 標 unavailable 的 preset
    （gemini-headless／antigravity-headless）不在本矩陣——固定缺項而非
    runtime skip；升級前提見 docs/backend-matrix.md。
    """

    def _smoke(self, preset_name: str) -> None:
        import yaml

        preset = backends.PRESETS[preset_name]
        # 第一層：executable/version probe（互動環境；快、免 LLM 配額）。
        # live=True 才真跑 `<exe> --version`——本層意圖即實際版本 probe，非解析級。
        probe = backends.probe_preset(preset, env=dict(os.environ), timeout=60,
                                      live=True)
        if probe.ok is not True:
            self.skipTest(f"{preset_name} 本機不可用（version probe）：{probe.detail}")
        argv = [probe.executable] + list(preset.argv_template[1:])
        # 第二層：launch probe——一次極小 prompt 真喚起，auth/配額/PATH 故障
        # 在此轉 skip（誠實回報），其後蒸餾失敗才算真 finding。
        try:
            AgentExecClient(argv, timeout=120).run('請只輸出 ["ok"]，不要其他文字')
        except AgentExecError as exc:
            self.skipTest(f"{preset_name} 本機不可用（launch probe）：{exc}")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            canonical = paths.atomizer_config_path()
            document = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            document["known_projects_file"] = str(projects)
            document["external_agents"]["profiles"] = [{
                "id": preset_name,
                "enabled": True,
                "tier": 1,
                "priority": 1,
                "traits": ["live-smoke"],
                "task_classes": ["atomization"],
                "model": preset_name,
                "supported_models": [preset_name],
                "effort": "medium",
                "supported_efforts": ["medium"],
                "timeout": _LIVE_TIMEOUT_SECONDS,
                "argv": argv,
            }]
            canonical.write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "atomize", "--memory-root", str(root),
                    "--now", "2026-07-10T03:00:00Z",
                    "--promoter", "llm",
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            slice_paths = sorted((root / "knowledge").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1, buf.getvalue())
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)
            # 驗收證據（backend、輸出 slice 數）——workflow 由測試輸出擷取進 PR body
            print(json.dumps({
                "smoke": "atomize-live", "backend": preset_name,
                "argv": argv, "slices": len(slice_paths),
            }, ensure_ascii=False), file=sys.stderr)

    def test_live_claude_headless(self):
        self._smoke("claude-headless")

    def test_live_codex_headless(self):
        self._smoke("codex-headless")

    def test_live_copilot_headless(self):
        self._smoke("copilot-headless")


FIXTURE_LEGACY = FIXTURE


@unittest.skipUnless(
    os.environ.get("PSC_ATOMIZE_LIVE"),
    "set PSC_ATOMIZE_LIVE=1 to enable the real co-gem zero-tool atomizer test",
)
class AtomizerLlmLiveTests(unittest.TestCase):
    def test_live_llm_atomize_with_256k_declaration_produces_gate_valid_slice(self):
        import yaml

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            canonical = paths.atomizer_config_path()
            document = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            document["known_projects_file"] = str(projects)
            document["context_window"] = 262144
            canonical.write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

            rc = cli.main(["atomize",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-06-02T03:00:00Z",
                    "--promoter",
                    "llm",
                ]
            )

            self.assertEqual(rc, 0)
            slice_paths = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1)
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)
            print(
                json.dumps(
                    {
                        "smoke": "atomize-live-256k-context-declaration",
                        "backend": "canonical-profile-router",
                        "declared_context_window": 262144,
                        "slices": len(slice_paths),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )


if __name__ == "__main__":
    unittest.main()
