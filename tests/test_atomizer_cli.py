from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import cli
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import cli as atomizer_cli

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-05-31T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha
"""


class AtomizeCliTests(unittest.TestCase):
    def test_dry_run_prints_summary_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True)
            raw.write_text(_RAW, encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                # #175 後預設 promoter=llm 會真跑 agent；本測試標的是 dry-run
                # 摘要行為，顯式改用 deterministic 的 identity。
                rc = cli.main(["atomize", "--memory-root", str(root),
                               "--now", "2026-05-31T03:00:00Z", "--dry-run",
                               "--promoter", "identity"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertGreaterEqual(payload["summary"]["slices"], 1)
            self.assertTrue(raw.exists())
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])


class AtomizeCliLlmTests(unittest.TestCase):
    def test_known_projects_missing_file_returns_empty_list(self):
        self.assertEqual(
            atomizer_cli._known_projects("/definitely/missing/projects.yaml"),
            [],
        )

    def test_known_projects_unreadable_file_returns_empty_list(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.yaml"
            path.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")

            with mock.patch("pathlib.Path.read_text", side_effect=OSError("boom")):
                self.assertEqual(atomizer_cli._known_projects(str(path)), [])

    def test_known_projects_ignores_unsafe_names(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.yaml"
            path.write_text("projects:\n  - paulshaclaw\n  - ../escaped\n", encoding="utf-8")

            self.assertEqual(atomizer_cli._known_projects(str(path)), ["paulshaclaw"])

    def test_agent_command_override_uses_shared_resolution(self):
        cfg, _ = atomizer_config.load_config(override_path=None)
        args = Namespace(promoter="llm", agent_command="scripts/build_release_artifact.py")

        with TemporaryDirectory() as tmp:
            with mock.patch.object(atomizer_cli, "ExternalAgentRouter") as router:
                atomizer_cli._build_promoter(args, cfg, Path(tmp))

        profiles = router.call_args.args[0]
        command = profiles[0].render_argv()
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertTrue(str(command[0]).endswith("scripts/build_release_artifact.py"))

    def test_promoter_llm_uses_stub_agent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
            raw.parent.mkdir(parents=True)
            raw.write_text(_RAW, encoding="utf-8")
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            override.write_text(f'known_projects_file: "{projects}"\n', encoding="utf-8")
            stub = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"
            buf = io.StringIO()

            with redirect_stdout(buf):
                rc = cli.main(["atomize",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T03:00:00Z",
                        "--promoter",
                        "llm",
                        "--override",
                        str(override),
                        "--agent-command",
                        f"{sys.executable} {stub}",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertTrue(list((root / "knowledge").rglob("*.md")))

    def test_cache_dir_is_shared_atomize_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = atomizer_cli._cache_dir(root)

            self.assertEqual(cache_dir, root / "runtime" / "cache" / "atomize")

    def test_build_promoter_llm_sets_output_token_env(self):
        import argparse
        from pathlib import Path
        from paulsha_hippo.atomizer import cli, config as cfgmod
        cfg, _ = cfgmod.load_config()
        args = argparse.Namespace(promoter="llm", agent_command=None)
        promoter = cli._build_promoter(args, cfg, Path("/tmp/does-not-matter"))
        from paulsha_hippo.agent_profiles import ExternalAgentRouter
        self.assertIsInstance(promoter._agent._inner, ExternalAgentRouter)
        self.assertTrue(promoter._agent._inner.profiles)
        self.assertNotIn("PSC_CLAUDE_GEMMA4_UPSTREAM_URL", vars(cfg))


if __name__ == "__main__":
    unittest.main()
