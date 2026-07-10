"""codex/copilot SessionStart 注入顯式 recall 指引（無 prompt-time hook 平台，#17）。

沿用 test_session_start_wiring 的 mock 手法：mock resolve_project、真跑
compute_brief_and_record + build_orientation（需 seed 一筆 knowledge 使 n>0）。
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def _seed_knowledge(root: Path):
    k = root / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: T\ncaptured_at: '2026-07-10T00:00:00Z'\n---\nbody\n", encoding="utf-8")


class RecallGuidanceTests(unittest.TestCase):
    def _ctx(self, module_name: str) -> str:
        import importlib
        mod = importlib.import_module(module_name)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_knowledge(root)
            payload = {"session_id": "sidG", "cwd": "/x"}
            out = io.StringIO()
            with mock.patch.dict("os.environ", {"PSC_MEMORY_ROOT": str(root)}), \
                 mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 mock.patch("paulsha_hippo.importer.project_resolver.resolve_project",
                            return_value="proj"), \
                 mock.patch("sys.stdout", out):
                mod.main()
            data = json.loads(out.getvalue())
            if "hookSpecificOutput" in data:
                return data["hookSpecificOutput"]["additionalContext"]
            return data["additionalContext"]

    def test_codex_session_start_injects_recall_guidance(self):
        ctx = self._ctx("paulsha_hippo.hooks.codex_session_start")
        self.assertIn("recall", ctx)
        self.assertIn("--tool codex", ctx)
        self.assertIn("--session-id sidG", ctx)
        self.assertNotIn("每次 prompt 後以短清單浮現", ctx)

    def test_copilot_session_start_injects_recall_guidance(self):
        ctx = self._ctx("paulsha_hippo.hooks.copilot_session_start")
        self.assertIn("recall", ctx)
        self.assertIn("--tool copilot-cli", ctx)
        self.assertNotIn("每次 prompt 後以短清單浮現", ctx)

    def test_claude_session_start_keeps_auto_shortlist_hint(self):
        ctx = self._ctx("paulsha_hippo.hooks.claude_session_start")
        self.assertIn("每次 prompt 後以短清單浮現", ctx)
        self.assertNotIn("mark-applied", ctx)


if __name__ == "__main__":
    unittest.main()
