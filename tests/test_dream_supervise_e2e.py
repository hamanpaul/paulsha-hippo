"""#10 原始 checklist：無 systemd 主機 dream supervise 前台實測一輪（spec §3.5.7）。

E2E 路徑：cli.main dream supervise --once → 等一個 interval → dream run
（--require-idle，--max-load 放大避免忙碌機器假 skip）→ atomize（llm promoter
＋fake-agent）→ janitor → moc。斷言蒸餾產物與 dream ledger。
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import cli
from paulsha_hippo.ledger import dream as dream_ledger
from paulsha_hippo.ledger import processing

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"
FAKE_AGENT = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"


class DreamSuperviseE2ETests(unittest.TestCase):
    def test_supervise_once_runs_full_dream_round_without_systemd(self):
        with TemporaryDirectory(dir=_REPO_ROOT) as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-07-10" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)

            # 隔離 HOME＋清掉 PSC_/HIPPO_ env，建立唯一 canonical runtime config。
            home = root / "home"
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            import yaml
            config_dir = home / ".config" / "paulsha-hippo"
            config_dir.mkdir(parents=True)
            source = _REPO_ROOT / "paulsha_hippo" / "atomizer" / "atomizer.yaml"
            document = yaml.safe_load(source.read_text(encoding="utf-8"))
            document["known_projects_file"] = str(projects)
            document["external_agents"]["profiles"] = [{
                "id": "fake-agent", "enabled": True, "tier": 1, "priority": 1,
                "traits": ["test"], "task_classes": ["atomization"],
                "model": "fake", "supported_models": ["fake"],
                "effort": "medium", "supported_efforts": ["medium"],
                "argv": [sys.executable, str(FAKE_AGENT)],
            }]
            (config_dir / "config.yaml").write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
            )

            clean_env = {k: v for k, v in os.environ.items()
                         if not k.startswith(("PSC_", "HIPPO_"))}
            clean_env["HOME"] = str(home)
            with mock.patch.dict(os.environ, clean_env, clear=True), mock.patch(
                "paulsha_hippo.ops._dream_timer_active", return_value=False
            ):
                rc = cli.main([
                    "dream", "supervise", "--interval", "1", "--once",
                    "--memory-root", str(root),
                    "--max-load", "1000000",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "promoted")
            slices = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slices), 1)
            last = dream_ledger.last_run(root)
            self.assertIsNotNone(last)
            self.assertIn(last["status"], ("ok", "partial"))
            self.assertIn("atomize", last.get("passes", {}))


if __name__ == "__main__":
    unittest.main()
