from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.janitor import config as janitor_config
from paulsha_hippo.janitor import scanner
from paulsha_hippo.janitor.config import JanitorConfig
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


def _init_repo_with_commit(path: Path) -> str:
    """建立真 git repo 並回傳其 HEAD sha。"""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _fresh_record(slice_id: str, project: str, commit: str) -> str:
    # commit is YAML-quoted: an all-digit sha like "0"*40 is otherwise parsed
    # as an octal/int literal by PyYAML and silently dropped from provenance
    # (KnowledgeRecord.provenance keeps only str values).
    return f"""---
memory_layer: knowledge
slice_id: {slice_id}
project: {project}
source_agent: claude
source_session: s1
source_artifact: a.md
captured_at: "2026-05-30T00:00:00Z"
provenance:
  repo: {project}
  commit: "{commit}"
  path: docs/x.md
---
body
"""


def _write_projects_yaml(base: Path, projects: dict[str, list[str]]) -> None:
    """按 `_roots_by_project`/`load_projects_config` 契約，把 project -> roots 寫進
    `<memory_root 上一層>/config/projects.yaml`（`base / "agents" / "memory"` 當
    memory_root 時，即 `base / "agents" / "config" / "projects.yaml"`）。"""
    lines = ["version: 1", "projects:"]
    for slug, roots in projects.items():
        lines.append(f"  {slug}:")
        lines.append(f"    slug: {slug}")
        lines.append("    roots:")
        for root in roots:
            lines.append(f"      - {root}")
    path = base / "agents" / "config" / "projects.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_CFG_COMMIT_ONLY = JanitorConfig(schema_version="1", default_decay_age_days=90, by_artifact_kind={},
                                 check_provenance_path=False, check_provenance_commit=True,
                                 decay_superseded=True)
_CFG_COMMIT_OFF = JanitorConfig(schema_version="1", default_decay_age_days=90, by_artifact_kind={},
                                check_provenance_path=False, check_provenance_commit=False,
                                decay_superseded=True)


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


class UsageDiagnosticsWarningTests(unittest.TestCase):
    """v5 requirement: usage-ledger diagnostics only warn when counter > 0."""

    def test_clean_ledger_emits_no_usage_diagnostics_warning(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            (led / "offered.jsonl").write_text(
                json.dumps({"tool": "claude-code", "session_id": "s1",
                            "ts": "2026-05-20T00:00:00Z", "offered": [{"sl_id": "sl-1"}]}) + "\n",
                encoding="utf-8",
            )
            (led / "memory_usage.jsonl").write_text(
                json.dumps({"tool": "claude-code", "session_id": "s1", "sl_id": "sl-1",
                            "source": "read", "ts": "2026-05-21T00:00:00Z"}) + "\n",
                encoding="utf-8",
            )
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            # #70: usage diagnostics window off usage_now (defaults to real
            # wall-clock time), not the run's `now`. Pin usage_now explicitly
            # here so this fixed 2026-05 event timestamp doesn't drift out of
            # the 30-day window as real time passes (would otherwise become a
            # time bomb: spuriously fails once run more than ~30 days after
            # the fixture dates were authored).
            result = scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=cfg_hash,
                                      now="2026-05-31T00:00:00Z", source_path_exists=lambda r: True,
                                      usage_now="2026-05-31T00:00:00Z")
            self.assertFalse(
                [w for w in result["warnings"] if w.startswith("usage ledger diagnostics")]
            )

    def test_malformed_usage_ledger_emits_single_bounded_warning(self):
        with TemporaryDirectory() as tmp:
            root, kroot = _setup(tmp)
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True)
            (led / "memory_usage.jsonl").write_text(
                "not-json\n"
                + json.dumps({"tool": "(unknown)", "session_id": "s1", "sl_id": "sl-1",
                              "source": "read", "ts": "2026-05-21T00:00:00Z"}) + "\n",
                encoding="utf-8",
            )
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            result = scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=cfg_hash,
                                      now="2026-05-31T00:00:00Z", source_path_exists=lambda r: True,
                                      usage_now="2026-05-31T00:00:00Z")
            usage_warnings = [w for w in result["warnings"] if w.startswith("usage ledger diagnostics")]
            self.assertEqual(len(usage_warnings), 1)
            self.assertIn("json_decode_error", usage_warnings[0])
            self.assertIn("missing_tool", usage_warnings[0])
            # ledger itself must be untouched by the scan
            content = (led / "memory_usage.jsonl").read_text(encoding="utf-8")
            self.assertTrue(content.startswith("not-json\n"))


class ScannerProvenanceCommitTests(unittest.TestCase):
    """Review round 1, finding #2: scanner-level coverage for the default
    ``source_commit_exists`` closure (``_roots_by_project`` + the multi-root
    aggregation any-True->True / all-False->False / else->None) — previously
    only exercised by a deleted manual smoke test, per task-16-report.md."""

    def test_default_closure_aggregates_multi_root_commit_checks(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "agents" / "memory"
            kroot = base / "knowledge"
            kroot.mkdir(parents=True)

            # proj-mix: one real repo + one existing-but-non-git root.
            repo_a = base / "repo-a"; repo_a.mkdir()
            head_a = _init_repo_with_commit(repo_a)
            plain_b = base / "plain-b"; plain_b.mkdir()

            # proj-both: two real repos, neither containing the target commit.
            repo_c = base / "repo-c"; repo_c.mkdir(); _init_repo_with_commit(repo_c)
            repo_d = base / "repo-d"; repo_d.mkdir(); _init_repo_with_commit(repo_d)

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PSC_CONFIG_ROOT", None)
                _write_projects_yaml(base, {
                    "proj-mix": [str(repo_a), str(plain_b)],
                    "proj-both": [str(repo_c), str(repo_d)],
                })

                absent_sha = "0" * 40
                (kroot / "sl-present.md").write_text(
                    _fresh_record("sl-present", "proj-mix", head_a), encoding="utf-8")
                (kroot / "sl-none.md").write_text(
                    _fresh_record("sl-none", "proj-mix", absent_sha), encoding="utf-8")
                (kroot / "sl-false.md").write_text(
                    _fresh_record("sl-false", "proj-both", absent_sha), encoding="utf-8")

                result = scanner.run_scan(
                    memory_root, knowledge_root=kroot, config=_CFG_COMMIT_ONLY,
                    config_hash="h", now="2026-05-31T00:00:00Z", dry_run=True,
                )

            by_id = {e["record_id"]: e for e in result["plan"]}
            # commit present in repo_a (any True wins over the non-git None) -> no decay
            self.assertNotIn("sl-present", by_id)
            # absent in repo_a (False) + non-git plain_b (None) -> aggregate None -> no decay
            self.assertNotIn("sl-none", by_id)
            # absent in both real repos (False, False) -> aggregate False -> source_invalid
            self.assertIn("sl-false", by_id)
            self.assertEqual(by_id["sl-false"]["reason"], "source_invalid")
            self.assertEqual(by_id["sl-false"]["detail"]["check"], "provenance_commit")
            self.assertEqual(result["summary"]["decayed"], 1)

    def test_default_closure_is_lazy_when_flag_off(self) -> None:
        """Review round 1, finding #3: a flag-off scan must perform no
        projects.yaml read — the default closure resolves ``_roots_by_project``
        lazily on first call, and ``_decide_decay`` never calls the checker at
        all when ``check_provenance_commit`` is False."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "agents" / "memory"
            kroot = base / "knowledge"
            kroot.mkdir(parents=True)
            (kroot / "sl-1.md").write_text(
                _fresh_record("sl-1", "proj-mix", "0" * 40), encoding="utf-8")

            with mock.patch.object(scanner, "_roots_by_project") as mocked:
                scanner.run_scan(
                    memory_root, knowledge_root=kroot, config=_CFG_COMMIT_OFF,
                    config_hash="h", now="2026-05-31T00:00:00Z", dry_run=True,
                )
            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
