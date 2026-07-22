from __future__ import annotations

import io
import os
import shutil
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from paulsha_hippo import cli as memory_cli
from paulsha_hippo.atomizer.config import AtomizerConfig, AtomizerConfigError, resolve_command_argv
from paulsha_hippo.skillopt import cli as skillopt_cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SANDBOX_ROOT = _REPO_ROOT / ".test-work" / "skillopt-cli"
_VALID_SKILL = "---\nname: atomize\n---\nbody\n"
_CFG = AtomizerConfig(
    schema_version="1",
    boundary_patterns=(r"^#{1,6}\s",),
    max_fragment_chars=8000,
    artifact_kind_map={},
    phase_map={},
    default_artifact_kind="report",
    default_phase="review",
    skill_path="skills/atomize-knowledge-slice.md",
)


class SkilloptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _SANDBOX_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        if _SANDBOX_ROOT.exists() and not any(_SANDBOX_ROOT.iterdir()):
            _SANDBOX_ROOT.rmdir()

    def test_run_optimize_reports_friendly_no_validation_items_message(self) -> None:
        skill_path = self.root / "skill.md"
        skill_path.write_text(_VALID_SKILL, encoding="utf-8")
        build_result = {
            "train": [{"id": "t1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
            "val": [],
        }
        calls = {"rollout": 0, "score": 0, "optimizer": 0}

        def _mark(name: str):
            def inner():
                calls[name] += 1
                return lambda *args, **kwargs: None

            return inner

        buf = io.StringIO()
        with (
            mock.patch("paulsha_hippo.skillopt.cli.build_valset", return_value=build_result),
            redirect_stdout(buf),
        ):
            rc = skillopt_cli.run_optimize(
                inbox_root=self.root / "inbox",
                reference_root=self.root / "notes",
                skill_path=skill_path,
                record_path=self.root / "skillopt.jsonl",
                now="2026-06-04T00:00:00Z",
                make_rollout=_mark("rollout"),
                make_score=_mark("score"),
                make_optimizer=_mark("optimizer"),
                budget=1,
            )

        self.assertEqual(rc, 2)
        self.assertEqual(calls, {"rollout": 0, "score": 0, "optimizer": 0})
        output = buf.getvalue().lower()
        self.assertIn("no validation items", output)
        self.assertIn("run importer first", output)
        self.assertIn("min_project_sample", output)
        self.assertIn("val_ratio", output)

    def test_load_skillopt_config_returns_defaults_when_optional_file_missing(self) -> None:
        config = skillopt_cli.load_skillopt_config(
            atomizer_cfg=_CFG,
            config_path=self.root / "missing-skillopt.yaml",
        )

        self.assertEqual(config.judge_command, resolve_command_argv(_CFG.agent_exec_command))
        self.assertEqual(config.alpha, 0.4)
        self.assertEqual(config.val_ratio, 0.2)
        self.assertEqual(config.min_project_sample, 2)
        self.assertEqual(config.judge_timeout, 600)

    def test_load_skillopt_config_reads_yaml_overrides(self) -> None:
        config_path = self.root / "skillopt.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "judge_command:",
                    "  - python3",
                    "  - -m",
                    "  - judge.demo",
                    "alpha: 0.65",
                    "val_ratio: 0.35",
                    "min_project_sample: 5",
                    "judge_timeout: 123",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        config = skillopt_cli.load_skillopt_config(atomizer_cfg=_CFG, config_path=config_path)

        self.assertEqual(config.judge_command, ("python3", "-m", "judge.demo"))
        self.assertEqual(config.alpha, 0.65)
        self.assertEqual(config.val_ratio, 0.35)
        self.assertEqual(config.min_project_sample, 5)
        self.assertEqual(config.judge_timeout, 123)

    def test_load_skillopt_config_uses_agents_root_default_path(self) -> None:
        agents_root = self.root / "custom-agents"
        config_path = agents_root / "config" / "skillopt.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "\n".join(
                [
                    "judge_command: python3 -m judge.demo",
                    "alpha: 0.75",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"PSC_AGENTS_ROOT": str(agents_root)}, clear=False):
            config = skillopt_cli.load_skillopt_config(atomizer_cfg=_CFG)

        self.assertEqual(config.judge_command, ("python3", "-m", "judge.demo"))
        self.assertEqual(config.alpha, 0.75)

    def test_run_optimize_dry_run_skips_optimizer_factory(self) -> None:
        skill_path = self.root / "skill.md"
        skill_path.write_text(_VALID_SKILL, encoding="utf-8")
        build_result = {
            "train": [{"id": "t1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
            "val": [{"id": "v1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
        }
        optimizer_calls = {"factory": 0, "runner": 0}

        def make_optimizer():
            optimizer_calls["factory"] += 1

            def optimizer(text, failures):
                optimizer_calls["runner"] += 1
                return text

            return optimizer

        buf = io.StringIO()
        with (
            mock.patch("paulsha_hippo.skillopt.cli.build_valset", return_value=build_result),
            redirect_stdout(buf),
        ):
            rc = skillopt_cli.run_optimize(
                inbox_root=self.root / "inbox",
                reference_root=self.root / "notes",
                skill_path=skill_path,
                record_path=self.root / "skillopt.jsonl",
                now="2026-06-04T00:00:00Z",
                make_rollout=lambda: (lambda skill_text, fragments: []),
                make_score=lambda: (lambda output, gold: 0.5),
                make_optimizer=make_optimizer,
                budget=3,
                dry_run=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(optimizer_calls, {"factory": 0, "runner": 0})
        self.assertIn('"dry_run": true', buf.getvalue().lower())

    def test_run_optimize_wraps_train_and_val_golds_for_judge_usage(self) -> None:
        skill_path = self.root / "skill.md"
        skill_path.write_text(_VALID_SKILL, encoding="utf-8")
        build_result = {
            "train": [{"id": "t1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
            "val": [{"id": "v1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
        }
        captured: dict[str, object] = {}

        def fake_optimize_skill(*args, **kwargs):
            captured["train_set"] = kwargs["train_set"]
            captured["val_set"] = kwargs["val_set"]
            return {
                "accepted": False,
                "baseline_score": 0.5,
                "candidate_score": 0.5,
                "improvement": 0.0,
                "reason": "rejected: no improvement",
            }

        with (
            mock.patch("paulsha_hippo.skillopt.cli.build_valset", return_value=build_result),
            mock.patch("paulsha_hippo.skillopt.cli.optimize_skill", side_effect=fake_optimize_skill),
            redirect_stdout(io.StringIO()),
        ):
            rc = skillopt_cli.run_optimize(
                inbox_root=self.root / "inbox",
                reference_root=self.root / "notes",
                skill_path=skill_path,
                record_path=self.root / "skillopt.jsonl",
                now="2026-06-04T00:00:00Z",
                make_rollout=lambda: (lambda skill_text, fragments: []),
                make_score=lambda: (lambda output, gold: 0.5),
                make_optimizer=lambda: (lambda text, failures: text),
                budget=1,
            )

        self.assertEqual(rc, 0)
        train_set = captured["train_set"]
        val_set = captured["val_set"]
        self.assertFalse(train_set[0]["gold"]["judge"]["enabled"])
        self.assertTrue(val_set[0]["gold"]["judge"]["enabled"])

    def test_run_optimize_threads_loaded_config_to_build_valset(self) -> None:
        skill_path = self.root / "skill.md"
        skill_path.write_text(_VALID_SKILL, encoding="utf-8")
        seen: dict[str, object] = {}
        skillopt_config = skillopt_cli.SkillOptConfig(
            judge_command=("python3", "-m", "judge.demo"),
            alpha=0.55,
            val_ratio=0.35,
            min_project_sample=4,
            judge_timeout=123,
        )

        def fake_build_valset(**kwargs):
            seen.update(kwargs)
            return {
                "train": [{"id": "t1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
                "val": [{"id": "v1", "input": [], "gold": {"project": "p", "reference_slices": []}}],
            }

        with (
            mock.patch("paulsha_hippo.skillopt.cli.build_valset", side_effect=fake_build_valset),
            redirect_stdout(io.StringIO()),
        ):
            rc = skillopt_cli.run_optimize(
                inbox_root=self.root / "inbox",
                reference_root=self.root / "notes",
                skill_path=skill_path,
                record_path=self.root / "skillopt.jsonl",
                now="2026-06-04T00:00:00Z",
                make_rollout=lambda: (lambda skill_text, fragments: []),
                make_score=lambda: (lambda output, gold: 0.5),
                make_optimizer=lambda: (lambda text, failures: text),
                config=_CFG,
                skillopt_config=skillopt_config,
            )

        self.assertEqual(rc, 0)
        self.assertIs(seen["config"], _CFG)
        self.assertEqual(seen["val_ratio"], 0.35)
        self.assertEqual(seen["min_project_sample"], 4)

    def test_build_default_hooks_threads_skillopt_judge_settings(self) -> None:
        skillopt_config = skillopt_cli.SkillOptConfig(
            judge_command=("python3", "-m", "judge.demo"),
            alpha=0.55,
            val_ratio=0.25,
            min_project_sample=3,
            judge_timeout=123,
        )
        seen: dict[str, object] = {}

        def fake_rollout(agent, known_projects, config):
            seen["rollout_args"] = (agent, tuple(known_projects), config)
            return "rollout"

        def fake_score(judge, *, alpha):
            seen["score_args"] = (judge, alpha)
            return "score"

        def fake_optimizer():
            seen["optimizer_built"] = True
            return "optimizer"

        with (
            mock.patch(
                "paulsha_hippo.skillopt.cli.make_atomize_rollout",
                side_effect=fake_rollout,
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.make_hybrid_score",
                side_effect=fake_score,
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.make_acp_optimizer",
                side_effect=fake_optimizer,
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.atomizer_cli._known_projects",
                return_value=["proj-a"],
            ),
        ):
            make_rollout, make_score, make_optimizer = skillopt_cli._build_default_hooks(_CFG, skillopt_config)
            self.assertEqual(make_rollout(), "rollout")
            self.assertEqual(make_score(), "score")
            self.assertEqual(make_optimizer(), "optimizer")
            self.assertEqual(seen["rollout_args"][0].__class__.__name__, "ExternalAgentRouter")
            self.assertEqual(seen["score_args"][0].__class__.__name__, "ExternalAgentRouter")
            self.assertEqual(seen["score_args"][0].task_class, "skillopt")
            self.assertEqual(seen["score_args"][0].profiles[0].id, "skillopt-custom")
            self.assertEqual(seen["score_args"][1], 0.55)

    def test_main_resolves_default_skill_path_outside_repo_root(self) -> None:
        outside_cwd = self.root / "outside-cwd"
        outside_cwd.mkdir(parents=True, exist_ok=True)
        seen: dict[str, object] = {}
        skillopt_config = skillopt_cli.SkillOptConfig(
            judge_command=("python3", "-m", "judge.demo"),
            alpha=0.55,
            val_ratio=0.35,
            min_project_sample=4,
            judge_timeout=123,
        )

        with (
            mock.patch(
                "paulsha_hippo.skillopt.cli.atomizer_config.load_config",
                return_value=(_CFG, "cfg-hash"),
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.load_skillopt_config",
                return_value=skillopt_config,
            ) as load_skillopt_config,
            mock.patch(
                "paulsha_hippo.skillopt.cli._build_default_hooks",
                side_effect=lambda atomizer_cfg, loaded_skillopt_config: seen.update(
                    hook_atomizer_config=atomizer_cfg,
                    hook_skillopt_config=loaded_skillopt_config,
                )
                or (
                    lambda: (lambda skill_text, fragments: []),
                    lambda: (lambda output, gold: 0.0),
                    lambda: (lambda text, failures: text),
                ),
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.run_optimize",
                side_effect=lambda **kwargs: seen.update(kwargs) or 0,
            ),
        ):
            cwd = Path.cwd()
            try:
                os.chdir(outside_cwd)
                rc = skillopt_cli.main(
                    [
                        "run",
                        "--memory-root",
                        str(self.root / "memory"),
                        "--reference-root",
                        str(self.root / "notes"),
                        "--dry-run",
                        "--now",
                        "2026-06-04T00:00:00Z",
                    ]
                )
            finally:
                os.chdir(cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(
            seen["skill_path"],
            _REPO_ROOT / "paulsha_hippo" / "atomizer" / "skills" / "atomize-knowledge-slice.md",
        )
        self.assertIs(seen["config"], _CFG)
        self.assertIs(seen["skillopt_config"], skillopt_config)
        self.assertIs(seen["hook_atomizer_config"], _CFG)
        self.assertIs(seen["hook_skillopt_config"], skillopt_config)
        load_skillopt_config.assert_called_once_with(atomizer_cfg=_CFG)

    def test_main_handles_atomizer_config_error_with_friendly_message(self) -> None:
        buf = io.StringIO()

        with (
            mock.patch(
                "paulsha_hippo.skillopt.cli.atomizer_config.load_config",
                side_effect=AtomizerConfigError("bad override"),
            ),
            redirect_stdout(buf),
        ):
            rc = skillopt_cli.main(["run"])

        self.assertEqual(rc, 2)
        self.assertEqual(buf.getvalue().strip(), "skillopt: cannot load atomizer config: bad override")

    def test_memory_cli_routes_skillopt_run_to_friendly_no_validation_path(self) -> None:
        buf = io.StringIO()

        with (
            mock.patch(
                "paulsha_hippo.skillopt.cli.atomizer_config.load_config",
                return_value=(_CFG, "cfg-hash"),
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli._build_default_hooks",
                return_value=(
                    lambda: (lambda skill_text, fragments: []),
                    lambda: (lambda output, gold: 0.0),
                    lambda: (lambda text, failures: text),
                ),
            ),
            mock.patch(
                "paulsha_hippo.skillopt.cli.build_valset",
                return_value={"train": [], "val": []},
            ),
            redirect_stdout(buf),
        ):
            rc = memory_cli.main(["skillopt",
                    "run",
                    "--memory-root",
                    str(self.root / "memory"),
                    "--reference-root",
                    str(self.root / "notes"),
                    "--dry-run",
                ]
            )

        self.assertEqual(rc, 2)
        self.assertIn("no validation items", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
