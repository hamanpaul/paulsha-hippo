"""SessionEnd no longer records cited/matched usage events (retired)."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "paulsha_hippo" / "hooks" / "claude_session_end.py"


def _run_hook(stdin_obj: dict, memory_root: Path):
    env = {**os.environ, "PSC_MEMORY_ROOT": str(memory_root)}
    return subprocess.run(
        ["python3", str(HOOK)],
        cwd=REPO_ROOT,
        input=json.dumps(stdin_obj),
        env=env,
        capture_output=True,
        text=True,
    )


def _offered(root: Path, tool: str, sid: str):
    d = root / "runtime" / "wakeup"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tool}__{sid}.json").write_text(json.dumps({
        "session_id": sid, "tool": tool, "ts": "2026-06-25T00:00:00Z",
        "offered": [{"id": "sl-1234567890abcdef", "title": "Some Long Title Here"}],
    }, ensure_ascii=False), encoding="utf-8")


def _transcript(root: Path, lines: list[dict]) -> Path:
    p = root / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return p


class SessionEndUsageRetiredTests(unittest.TestCase):
    def test_session_end_does_not_write_cited_matched_event(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            (root / "runtime" / "queue").mkdir(parents=True)
            (root / "log").mkdir(parents=True)
            # Conditions that previously triggered a cited/matched record:
            _offered(root, "claude-code", "s1")
            tp = _transcript(root, [
                {"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "我用了 [[sl-1234567890abcdef]] 來解決。"}]}},
            ])
            result = _run_hook(
                {"session_id": "s1", "project": "paulshaclaw",
                 "transcript_path": str(tp)}, root)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            # Queue payload still written (hook core unaffected).
            self.assertEqual(
                len(list((root / "runtime" / "queue").glob("claude-code__s1__*.json"))),
                1,
            )
            # cited/matched usage event retired -> SessionEnd writes nothing.
            self.assertFalse(
                (root / "runtime" / "ledger" / "memory_usage.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
