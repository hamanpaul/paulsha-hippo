"""Tests that each SessionStart entrypoint emits the orientation (#148 → Plan 2).

The three entrypoints must call ``compute_brief_and_record`` (not the bare
``compute_brief``) so the SessionStart orientation flows into their emitted
output. SessionStart no longer writes a session-wide offered file (offered is
recorded by the Plan 1 prompt-retrieval path instead).

We mock ``resolve_project`` and ``build_orientation`` so the real
``compute_brief_and_record`` runs and the orientation reaches stdout.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Entrypoint modules do ``import _bootstrap`` at module load (the hooks dir is
# normally sys.path[0] when run as a script). Make that importable here.
_HOOKS_DIR = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


_ORIENTATION = (
    "# 記憶 — proj\n\n記憶系統已啟用（本專案約 2 筆 knowledge）。"
    "與當前任務相關的記憶會在每次 prompt 後以短清單浮現；用 Read 開啟清單中列出的絕對路徑即取全文。"
)


class SessionStartWiringTests(unittest.TestCase):
    def _run(self, module_name: str, tool: str):
        import importlib

        mod = importlib.import_module(module_name)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"session_id": "wire1", "cwd": "/x"}
            out = io.StringIO()
            with mock.patch.dict("os.environ", {"PSC_MEMORY_ROOT": str(root)}), \
                 mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 mock.patch(
                     "paulsha_hippo.importer.project_resolver.resolve_project",
                     return_value="proj",
                 ), \
                 mock.patch(
                     "paulsha_hippo.wakeup.builder.build_orientation",
                     return_value=_ORIENTATION,
                 ), \
                 mock.patch("sys.stdout", out):
                mod.main()
            # entrypoint must wire the orientation into its emitted output
            self.assertIn("Read", out.getvalue(), f"{module_name} did not emit orientation")
            # SessionStart no longer writes a session-wide offered file
            self.assertFalse(
                (root / "runtime" / "wakeup" / f"{tool}__wire1.json").exists(),
                f"{module_name} unexpectedly wrote a session-wide offered file",
            )

    def test_claude_emits_orientation(self):
        self._run("paulsha_hippo.hooks.claude_session_start", "claude-code")

    def test_codex_emits_orientation(self):
        self._run("paulsha_hippo.hooks.codex_session_start", "codex")

    def test_copilot_emits_orientation(self):
        # copilot's TOOL constant is "copilot-cli".
        self._run("paulsha_hippo.hooks.copilot_session_start", "copilot-cli")


if __name__ == "__main__":
    unittest.main()
