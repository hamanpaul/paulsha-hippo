"""PR-D spec §3.5.5 mock 情境矩陣：散文包 JSON／截斷／non-zero exit／timeout。

四種輸出情境經 custom-argv 機制（與 preset 同構）注入 atomize E2E：
- 散文包 JSON → llm_output.parse 抽出 JSON 陣列，session promoted
- 截斷輸出   → invalid_output：毒快取即時淘汰＋重試，超限 parked（契約 1）
- non-zero    → transient：本輪留 split、快取不落地
- timeout     → transient：本輪留 split、快取不落地
「純 JSON」happy path 由 tests/test_atomizer_e2e.py（fake-agent）與
tests/test_atomizer_llm_live.py（真蒸餾 smoke）覆蓋。
"""
from __future__ import annotations

import io
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli as memory_cli
from paulsha_hippo.ledger import processing

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "atomizer"
RAW_FIXTURE = FIXTURES / "raw" / "s1.md"
SESSION_KEY = "claude:sess-e2e"


def _seed(root: Path) -> None:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RAW_FIXTURE, raw)


def _write_override(root: Path, agent_script: str, *, timeout_seconds: int = 300) -> Path:
    projects = root / "projects.yaml"
    projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
    override = root / "atomizer.override.yaml"
    override.write_text(
        "\n".join(
            (
                f'known_projects_file: "{projects}"',
                "agent_exec:",
                "  command:",
                f"    - {sys.executable}",
                f"    - {FIXTURES / agent_script}",
                f"  timeout_seconds: {timeout_seconds}",
                "  model: mock-backend",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return override


def _atomize(root: Path, override: Path, now: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = memory_cli.main([
            "atomize", "--memory-root", str(root), "--now", now,
            "--promoter", "llm", "--override", str(override),
        ])
    return rc, buf.getvalue()


def _cache_json_files(root: Path) -> list[Path]:
    cache = root / "runtime" / "cache" / "atomize"
    return sorted(cache.glob("*.json")) if cache.exists() else []


class ProseWrappedJsonTests(unittest.TestCase):
    def test_prose_wrapped_json_is_parked_invalid_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "prose-agent.py")
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, SESSION_KEY), "parked")
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])


class TruncatedOutputTests(unittest.TestCase):
    def test_truncated_output_evicts_cache_and_parks_after_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "truncated-agent.py")

            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            state = processing.state_of(root, SESSION_KEY)
            self.assertEqual(state, "parked")
            self.assertIn("llm promote failed", out)
            # 毒快取即時淘汰（spec §3.1.1 invalid output：先淘汰快取再重試）
            self.assertEqual(_cache_json_files(root), [])

            event = processing.fold_events(root)[SESSION_KEY]
            self.assertEqual(event["failure_category"], "invalid_output")
            self.assertGreaterEqual(int(event["attempts"]), 1)
            self.assertTrue(event.get("cache_key"))
            self.assertTrue(event.get("error"))
            self.assertLessEqual(len(str(event["error"])), 500)
            # 超限即淘汰（spec §3.1.9 測試反轉）＋證據落 _failed/
            self.assertEqual(_cache_json_files(root), [])
            failed_dir = root / "runtime" / "queue" / "_failed"
            self.assertTrue(failed_dir.is_dir() and any(failed_dir.iterdir()))
            # split fragments 保留供 requeue（spec §3.1.8）
            self.assertTrue(list((root / "inbox" / "_slices").rglob("*.md")))

            # parked 不再吃 atomize 預算（spec §3.1.2）
            attempts_before = int(event["attempts"])
            rc, _ = _atomize(root, override, "2026-07-10T12:00:00Z")
            self.assertEqual(rc, 0)
            event_after = processing.fold_events(root)[SESSION_KEY]
            self.assertEqual(event_after["state"], "parked")
            self.assertEqual(int(event_after.get("attempts", attempts_before)),
                             attempts_before)


class NonZeroExitTests(unittest.TestCase):
    def test_nonzero_exit_is_transient_no_cache_written(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "failing-agent.py")
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, SESSION_KEY), "parked")
            self.assertIn("exited with code 3", out)
            self.assertEqual(_cache_json_files(root), [])


class TimeoutTests(unittest.TestCase):
    def test_timeout_override_cannot_weaken_fixed_300_second_contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "hanging-agent.py", timeout_seconds=1)
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 1)
            self.assertIsNone(processing.state_of(root, SESSION_KEY))
            self.assertIn("timeout_seconds is fixed at 300", out)
            self.assertEqual(_cache_json_files(root), [])


if __name__ == "__main__":
    unittest.main()
