"""#7 遞迴自捕捉 + #8 空 session 汙染的兩層防護測試。

Layer 1（截取端）：agent_exec 注入 HIPPO_SELF_SESSION → capture hooks 早退。
Layer 2（治理端）：importer 對自捕捉/空 session 短路（不寫 inbox）。
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


if __name__ == "__main__":
    unittest.main()
