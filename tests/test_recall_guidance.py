"""SessionStart retrieval 提示依 capability matrix 分流（#17）。

codex（prompt-time inconclusive）→ 注入顯式 recall 指引；
copilot / claude（prompt-time supported、自動 shortlist 已接線）→ 保留預設提示。
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

    def test_copilot_session_start_keeps_auto_shortlist_hint(self):
        # capability matrix 2026-07-11 復測：copilot prompt-time（userPromptSubmitted）
        # = supported，自動 shortlist 已接線 → 不注入顯式 recall 指引（同 claude）。
        ctx = self._ctx("paulsha_hippo.hooks.copilot_session_start")
        self.assertIn("每次 prompt 後以短清單浮現", ctx)
        self.assertNotIn("--tool copilot-cli", ctx)

    def test_claude_session_start_keeps_auto_shortlist_hint(self):
        ctx = self._ctx("paulsha_hippo.hooks.claude_session_start")
        self.assertIn("每次 prompt 後以短清單浮現", ctx)
        self.assertNotIn("mark-applied", ctx)


class ShowCommandHintTests(unittest.TestCase):
    """全支線 review I1：兩個 hint 先前寫死 `hippo show ... --memory-root …`。

    `hippo` 不一定在 PATH 上（wheel／venv／pipx 部署各不相同，`hippo_invocation`
    就是為此存在），而字面 `…` 是 agent 抄不動的佔位符——照抄就是一條跑不起來的
    指令。兩者都要由 `format_show_command(root, ...)` 用真實 `--memory-root` 組出。
    """

    def test_format_show_command_uses_invocation_and_real_memory_root(self):
        from paulsha_hippo.hooks import _wakeup_common as wc

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = wc.format_show_command(root)
            self.assertTrue(bare.endswith(f"show --memory-root {root} --agent"))
            self.assertTrue(bare.startswith(" ".join(wc.hippo_invocation(root))))
            self.assertNotIn("--tool", bare)
            self.assertNotIn("--session-id", bare)
            attributed = wc.format_show_command(root, tool="codex", session_id="sidG")
            self.assertTrue(attributed.endswith("--agent --tool codex --session-id sidG"))

    def test_recall_guidance_hint_show_command_is_copy_pasteable(self):
        from paulsha_hippo.hooks import _wakeup_common as wc

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hint = wc.recall_guidance_hint(root, "codex", "sidG", "/x")
            self.assertNotIn("…", hint)
            self.assertNotIn("`hippo show", hint)
            self.assertIn(wc.format_show_command(root, tool="codex", session_id="sidG"), hint)


if __name__ == "__main__":
    unittest.main()
