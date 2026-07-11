"""spec §3.5.6 openai-compatible 真端點 smoke（integration profile，env gate）。

未設 HIPPO_SMOKE_OPENAI_BASE_URL 時整檔 skip——不進一般 CI。
本機有 gemma4/vLLM/ollama 類端點時：

    HIPPO_SMOKE_OPENAI_BASE_URL="$PSC_CLAUDE_GEMMA4_UPSTREAM_URL" \
    HIPPO_SMOKE_OPENAI_MODEL=<served-model-name> \
    python3 -m pytest tests/test_openai_smoke_integration.py -v -s

需要 Bearer key 的端點另設 HIPPO_SMOKE_OPENAI_API_KEY_ENV=<存 key 的 env 名>。
"""
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

from paulsha_hippo import cli
from paulsha_hippo.lib.lifecycle.gate import run_static_gate_check_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"

BASE_URL = os.environ.get("HIPPO_SMOKE_OPENAI_BASE_URL", "")
MODEL = os.environ.get("HIPPO_SMOKE_OPENAI_MODEL", "")
API_KEY_ENV = os.environ.get("HIPPO_SMOKE_OPENAI_API_KEY_ENV", "")


@unittest.skipUnless(
    BASE_URL, "set HIPPO_SMOKE_OPENAI_BASE_URL to run the real-endpoint smoke")
class OpenAiCompatibleSmokeTests(unittest.TestCase):
    def test_real_endpoint_distills_fixture_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            override.write_text(
                (
                    f'known_projects_file: "{projects}"\n'
                    "agent_exec:\n"
                    "  backend: openai-compatible\n"
                    f"  base_url: {BASE_URL}\n"
                    + (f"  api_key_env: {API_KEY_ENV}\n" if API_KEY_ENV else "")
                    + (f"  model: {MODEL}\n" if MODEL else "")
                    + "  timeout_seconds: 300\n"
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "atomize", "--memory-root", str(root),
                    "--now", "2026-07-10T03:00:00Z",
                    "--promoter", "llm", "--override", str(override),
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            slice_paths = sorted((root / "knowledge").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1, buf.getvalue())
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)
            print(json.dumps({
                "smoke": "openai-compatible", "base_url_set": True,
                "model": MODEL or "(config default)", "slices": len(slice_paths),
            }, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
