from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import config as janitor_config
from paulsha_hippo.janitor import scanner
from paulsha_hippo.ledger import lifecycle


_OLD_RECORD = """---
memory_layer: knowledge
slice_id: sl-1
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: a.md
captured_at: "2020-01-01T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
body
"""


_UNTITLED_RECORD = """---
memory_layer: knowledge
slice_id: sl-u1
project: github.com/hamanpaul/testpilot
title: untitled
source_agent: claude
source_session: s2
source_artifact: b.md
captured_at: "2026-06-22T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
## 語言政策
所有溝通使用 zh-TW。
"""


def _setup(tmp: str) -> tuple[Path, Path]:
    root = Path(tmp)
    kroot = root / "knowledge"
    kroot.mkdir(parents=True, exist_ok=True)
    (kroot / "sl-1.md").write_text(_OLD_RECORD, encoding="utf-8")
    return root, kroot


class ScannerTests(unittest.TestCase):
    def test_scan_writes_decayed_event(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            result = scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=cfg_hash,
                                      now="2026-05-31T00:00:00Z", source_path_exists=lambda r: True)
            self.assertEqual(result["summary"]["decayed"], 1)
            events = lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl")
            self.assertEqual(events[0]["reason"], "ttl_expired")

    def test_idempotent_second_run_emits_nothing(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            kwargs = dict(knowledge_root=kroot, config=cfg, config_hash=cfg_hash,
                          now="2026-05-31T00:00:00Z", source_path_exists=lambda r: True)
            scanner.run_scan(root, **kwargs)
            result2 = scanner.run_scan(root, **kwargs)
            self.assertEqual(result2["summary"]["decayed"], 0)
            self.assertEqual(len(lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl")), 1)

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            result = scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=cfg_hash,
                                      now="2026-05-31T00:00:00Z", dry_run=True, source_path_exists=lambda r: True)
            self.assertEqual(len(result["plan"]), 1)
            self.assertEqual(lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl"), [])


class ScannerLintTests(unittest.TestCase):
    def test_lint_counts_and_warnings_surface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            kroot.mkdir(parents=True)
            slice_path = kroot / "untitled--sl-u1.md"
            slice_path.write_text(_UNTITLED_RECORD, encoding="utf-8")
            cfg, cfg_hash = janitor_config.load_config(override_path=None)

            result = scanner.run_scan(
                root,
                knowledge_root=kroot,
                config=cfg,
                config_hash=cfg_hash,
                now="2026-07-02T00:00:00Z",
                dry_run=True,
                source_path_exists=lambda r: True,
            )

            self.assertEqual(result["summary"]["lint"], {"untitled": 1, "raw_remote_key": 0})
            lint_warnings = [warning for warning in result["warnings"] if warning.startswith("lint:")]
            self.assertEqual(len(lint_warnings), 1)
            self.assertTrue(slice_path.exists())
            self.assertEqual(lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl"), [])

    def test_clean_tree_has_zero_lint(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            cfg, cfg_hash = janitor_config.load_config(override_path=None)

            result = scanner.run_scan(
                root,
                knowledge_root=kroot,
                config=cfg,
                config_hash=cfg_hash,
                now="2026-05-31T00:00:00Z",
                source_path_exists=lambda r: True,
            )

            self.assertEqual(result["summary"]["lint"], {"untitled": 0, "raw_remote_key": 0})
            self.assertFalse([warning for warning in result["warnings"] if warning.startswith("lint:")])


if __name__ == "__main__":
    unittest.main()
