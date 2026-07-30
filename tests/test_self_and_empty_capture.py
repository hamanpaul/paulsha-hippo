"""#7 遞迴自捕捉 + #8 空 session 汙染 + trivial-session 三層防護測試。

Layer 1（截取端）：agent_exec 注入 HIPPO_SELF_SESSION → capture hooks 早退。
Layer 2（治理端）：importer 對自捕捉/空 session/trivial session 短路（不寫 inbox）。
  trivial-session 是 title.py 標題生成 prompt 的遞迴自捕捉——與 #7 同類問題的另一個
  來源：title.apply() 呼叫外部 CLI 生成標題時，該 CLI 子行程本身又觸發一次
  SessionEnd hook，把「生成標題用的 prompt」錯當成新的使用者 session 蒸餾進佇列。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paulsha_hippo.importer.pipeline import (
    ingest_queue_item,
    is_empty_session,
    is_self_capture,
    is_trivial_session,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / "paulsha_hippo" / "hooks"


def _base_payload(**over):
    p = {
        "tool": "claude-code",
        "session_id": "sid-x",
        "capture_scope": "session_end",
        "ended_at": "2026-07-07T10:00:00+00:00",
        "cwd": str(REPO_ROOT),
        "repo": "hamanpaul/paulshaclaw",
        "turn_count": 3,
        "user_prompts": ["implement X"],
        "assistant_summary": "did X",
        "touched_files": ["a.py"],
        "referenced_artifacts": [],
    }
    p.update(over)
    return p


class HookSelfSessionGuardTests(unittest.TestCase):
    """Layer 1：HIPPO_SELF_SESSION=1 時 capture hook 不寫 queue。"""

    HOOKS = [
        "claude_session_end.py",
        "codex_session_end.py",
        "copilot_session_end.py",
        "claude_precompact.py",
        "copilot_precompact.py",
    ]

    def _run_hook(self, name, memory_root, *, self_session):
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(memory_root.parent),
            "PSC_MEMORY_ROOT": str(memory_root),
            "PSC_IMPORTER_DISABLED": "1",
        }
        if self_session:
            env["HIPPO_SELF_SESSION"] = "1"
        payload = json.dumps(_base_payload(session_id=f"hook-{name}"))
        return subprocess.run(
            [sys.executable, str(HOOKS_DIR / name)],
            input=payload, text=True, capture_output=True,
            cwd=str(memory_root.parent), env=env, timeout=30,
        )

    def test_self_session_skips_all_capture_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory"
            for name in self.HOOKS:
                r = self._run_hook(name, mem, self_session=True)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
            queue = mem / "runtime" / "queue"
            items = list(queue.glob("*.json")) if queue.exists() else []
            self.assertEqual(items, [], f"self-session 仍寫入 queue: {items}")

    def test_normal_session_end_still_captures(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory"
            r = self._run_hook("claude_session_end.py", mem, self_session=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            items = list((mem / "runtime" / "queue").glob("*.json"))
            self.assertEqual(len(items), 1, "正常 session 應寫入 queue")


class AgentExecMarkerTests(unittest.TestCase):
    """Layer 1 源頭：AgentExecClient 對蒸餾子程序注入 HIPPO_SELF_SESSION=1。"""

    def test_subprocess_env_carries_self_marker(self):
        from paulsha_hippo.atomizer.agent_exec import AgentExecClient

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.sh"
            out = Path(tmp) / "env.txt"
            probe.write_text(
                f'#!/usr/bin/env bash\nprintf "%s" "${{HIPPO_SELF_SESSION:-MISSING}}" > "{out}"\necho ok\n',
                encoding="utf-8",
            )
            probe.chmod(0o755)
            client = AgentExecClient([str(probe)], timeout=10)
            result = client.run("distill this")
            self.assertEqual(result.strip(), "ok")
            self.assertEqual(out.read_text(encoding="utf-8"), "1")


class ImporterSkipTests(unittest.TestCase):
    """Layer 2：importer 對自捕捉/空 session 短路。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memory"
        self.queue = self.root / "runtime" / "queue"
        self.queue.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, payload):
        path = self.queue / f"{name}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def test_self_capture_prompt_is_skipped(self):
        payload = _base_payload(
            session_id="self-cap",
            user_prompts=[
                "請執行 skill：\n---\nname: atomize-knowledge-slice\n---\n# Atomize Knowledge Slice\n把單一 session 的 fragments 蒸餾成可驗證的 knowledge slices"
            ],
        )
        decision = ingest_queue_item(self._write("self-cap", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "self-skip")
        self.assertFalse((self.root / "inbox").exists() and any((self.root / "inbox").rglob("*.md")),
                         "self-skip 不應寫 inbox")
        self.assertFalse(list(self.queue.glob("*.json")), "queue 應被移除")

    def test_empty_session_is_skipped(self):
        payload = _base_payload(
            session_id="empty-1", turn_count=1,
            user_prompts=[], assistant_summary="", touched_files=[],
        )
        decision = ingest_queue_item(self._write("empty-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "empty-skip")
        self.assertFalse(list(self.queue.glob("*.json")))

    def test_real_session_still_written(self):
        payload = _base_payload(session_id="real-1")
        decision = ingest_queue_item(self._write("real-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "written")
        self.assertTrue(list((self.root / "inbox").rglob("*.md")))

    def test_predicates_direct(self):
        self.assertTrue(is_empty_session(
            {"user_prompts": [], "touched_files": [], "assistant_summary": "  ", "turn_count": 1}))
        self.assertFalse(is_empty_session(
            {"user_prompts": ["x"], "touched_files": [], "assistant_summary": "", "turn_count": 1}))
        self.assertTrue(is_self_capture(
            {"user_prompts": ["blah # Atomize Knowledge Slice blah"]}))
        self.assertFalse(is_self_capture({"user_prompts": ["normal work"]}))


class TrivialSessionSkipTests(unittest.TestCase):
    """Layer 2 補充：title 生成 prompt 遞迴自捕捉短路（trivial-skip）。

    2026-07-30 harness 驗證（``tools/validate_trivial_gate.py``，見 changelog）：對
    23,909 個歷史 written session 回測，title 生成 prompt 簽章對 promoted（530）／
    parked（9）誤殺數皆為 0，no-findings 捕獲率 99.9%（23,327/23,358 可驗證且未被
    既有 empty-skip/self-skip 攔下的列）。曾評估「turn_count<=1 + 無 touched_files +
    短 prompt」的泛化門檻，但在同一份資料上會誤殺 18 個真實 promoted session（例如
    2 字元 prompt 的 claude-code session、user_prompts 為空但 assistant_summary
    近 3000 字的 codex session）——故不採用，只留 title 生成 prompt 簽章這一條。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memory"
        self.queue = self.root / "runtime" / "queue"
        self.queue.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, payload):
        path = self.queue / f"{name}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def test_title_gen_recursive_prompt_is_skipped(self):
        """單輪微 session（title 生成 CLI 子行程自捕捉）被擋。"""
        from paulsha_hippo.importer import title

        recursive_prompt = title._PROMPT.format(prompt="一些先前的使用者需求", summary="先前 assistant 結論")
        payload = _base_payload(
            session_id="titlegen-1",
            turn_count=1,
            user_prompts=[recursive_prompt],
            assistant_summary="幫這個工作 session 命名",
            touched_files=[],
        )
        decision = ingest_queue_item(self._write("titlegen-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "trivial-skip")
        self.assertFalse(
            (self.root / "inbox").exists() and any((self.root / "inbox").rglob("*.md")),
            "trivial-skip 不應寫 inbox",
        )
        self.assertFalse(list(self.queue.glob("*.json")), "queue 應被移除")

    def test_atom_title_gen_recursive_prompt_is_skipped(self):
        from paulsha_hippo.importer import title

        recursive_prompt = title._ATOM_PROMPT.format(body="某個 atom 筆記內容")
        payload = _base_payload(
            session_id="atom-titlegen-1",
            turn_count=1,
            user_prompts=[recursive_prompt],
            assistant_summary="精簡標題",
            touched_files=[],
        )
        decision = ingest_queue_item(self._write("atom-titlegen-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "trivial-skip")

    def test_single_turn_with_touched_files_still_written(self):
        """帶 touched_files 的單輪放行——即使 prompt 很短也不可誤殺。"""
        payload = _base_payload(
            session_id="touch-1", turn_count=1,
            user_prompts=["ok"], assistant_summary="done", touched_files=["a.py"],
        )
        decision = ingest_queue_item(self._write("touch-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "written")

    def test_single_turn_with_long_prompt_still_written(self):
        """長 prompt 單輪放行。"""
        payload = _base_payload(
            session_id="long-1", turn_count=1,
            user_prompts=["請幫我詳細分析這個系統的架構與潛在風險，並列出具體的改善建議" * 10],
            assistant_summary="分析完成",
            touched_files=[],
        )
        decision = ingest_queue_item(self._write("long-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "written")

    def test_multi_turn_still_written(self):
        """多輪放行。"""
        payload = _base_payload(
            session_id="multi-1", turn_count=3,
            user_prompts=["ok", "繼續", "好"],
            assistant_summary="done",
            touched_files=[],
        )
        decision = ingest_queue_item(self._write("multi-1", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "written")

    def test_empty_session_priority_unchanged(self):
        """空 session 仍走 empty-skip（優先序不變）。"""
        payload = _base_payload(
            session_id="empty-2", turn_count=1,
            user_prompts=[], assistant_summary="", touched_files=[],
        )
        decision = ingest_queue_item(self._write("empty-2", payload), memory_root=self.root)
        self.assertEqual(decision["status"], "empty-skip")

    def test_predicates_direct(self):
        from paulsha_hippo.importer import title

        recursive_prompt = title._PROMPT.format(prompt="x", summary="y")
        atom_prompt = title._ATOM_PROMPT.format(body="z")
        self.assertTrue(is_trivial_session({"user_prompts": [recursive_prompt]}))
        self.assertTrue(is_trivial_session({"user_prompts": [atom_prompt]}))
        self.assertFalse(is_trivial_session({"user_prompts": ["正常的使用者需求"]}))
        self.assertFalse(is_trivial_session({"user_prompts": []}))

    def test_predicate_is_tool_agnostic(self):
        """判準對 codex/copilot session 也成立——NormalizedSession 本身就是 tool-agnostic。"""
        from paulsha_hippo.importer import title

        recursive_prompt = title._PROMPT.format(prompt="x", summary="y")
        for tool in ("codex", "copilot-cli"):
            with self.subTest(tool=tool):
                self.assertTrue(is_trivial_session({"tool": tool, "user_prompts": [recursive_prompt]}))


if __name__ == "__main__":
    unittest.main()
