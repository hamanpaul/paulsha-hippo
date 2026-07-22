from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paulsha_hippo import paths
from paulsha_hippo.atomizer import cli as atomizer_cli
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.agent_profiles import (
    FIXED_TIMEOUT_SECONDS,
    AgentProfile,
    ExternalAgentRouter,
)

from .loop import SkillOptError, optimize_skill
from .optimizer_acp import make_acp_optimizer
from .rollout import make_atomize_rollout
from .scorer import make_hybrid_score
from .valset import build_valset

HookFactory = Callable[[], Callable[..., Any]]
DEFAULT_ALPHA = 0.4
DEFAULT_VAL_RATIO = 0.2
DEFAULT_MIN_PROJECT_SAMPLE = 2
DEFAULT_JUDGE_TIMEOUT = 600


@dataclass(frozen=True)
class SkillOptConfig:
    judge_command: tuple[str, ...]
    alpha: float = DEFAULT_ALPHA
    val_ratio: float = DEFAULT_VAL_RATIO
    min_project_sample: int = DEFAULT_MIN_PROJECT_SAMPLE
    judge_timeout: int = DEFAULT_JUDGE_TIMEOUT


class SkillOptConfigError(ValueError):
    """Raised when the optional skillopt config is invalid."""


def _default_skillopt_config_path() -> Path:
    return paths.agents_path("config", "skillopt.yaml")


def _default_skillopt_config(
    atomizer_cfg: atomizer_config.AtomizerConfig | None,
) -> SkillOptConfig:
    judge_command = (
        tuple(atomizer_config.resolve_command_argv(atomizer_cfg.agent_exec_command))
        if atomizer_cfg is not None
        else ()
    )
    return SkillOptConfig(judge_command=judge_command)


def _read_optional_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillOptConfigError(f"cannot read {path}: {exc}") from exc

    try:
        import yaml

        payload = yaml.safe_load(text) or {}
    except ImportError:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillOptConfigError(f"cannot parse {path}: {exc}") from exc
    except Exception as exc:
        raise SkillOptConfigError(f"cannot parse {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise SkillOptConfigError(f"{path} root must be a mapping")
    return dict(payload)


def _parse_command(value: object, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        command = tuple(shlex.split(value))
    elif isinstance(value, (list, tuple)):
        command_list: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item:
                raise SkillOptConfigError(f"{field_name}[{index}] must be a non-empty string")
            command_list.append(item)
        command = tuple(command_list)
    else:
        raise SkillOptConfigError(f"{field_name} must be a string or list")
    if not command:
        raise SkillOptConfigError(f"{field_name} must not be empty")
    return command


def _parse_ratio(value: object, *, field_name: str, default: float) -> float:
    parsed = float(default if value is None else value)
    if not 0.0 <= parsed <= 1.0:
        raise SkillOptConfigError(f"{field_name} must be between 0 and 1 inclusive")
    return parsed


def _parse_positive_int(value: object, *, field_name: str, default: int) -> int:
    parsed = int(default if value is None else value)
    if parsed <= 0:
        raise SkillOptConfigError(f"{field_name} must be positive")
    return parsed


def load_skillopt_config(
    *,
    atomizer_cfg: atomizer_config.AtomizerConfig | None,
    config_path: Path | None = None,
) -> SkillOptConfig:
    resolved = (
        Path(config_path).expanduser()
        if config_path is not None
        else _default_skillopt_config_path()
    )
    defaults = _default_skillopt_config(atomizer_cfg)
    if not resolved.exists():
        return defaults

    payload = _read_optional_mapping(resolved)
    judge_command = payload.get("judge_command")
    return SkillOptConfig(
        judge_command=tuple(
            atomizer_config.resolve_command_argv(
                _parse_command(
                    defaults.judge_command if judge_command is None else judge_command,
                    field_name="judge_command",
                )
            )
        ),
        alpha=_parse_ratio(payload.get("alpha"), field_name="alpha", default=defaults.alpha),
        val_ratio=_parse_ratio(
            payload.get("val_ratio"),
            field_name="val_ratio",
            default=defaults.val_ratio,
        ),
        min_project_sample=_parse_positive_int(
            payload.get("min_project_sample"),
            field_name="min_project_sample",
            default=defaults.min_project_sample,
        ),
        judge_timeout=_parse_positive_int(
            payload.get("judge_timeout"),
            field_name="judge_timeout",
            default=defaults.judge_timeout,
        ),
    )


def _copy_gold_with_judge(gold: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    copied = dict(gold)
    judge = copied.get("judge")
    judge_config = dict(judge) if isinstance(judge, dict) else {}
    judge_config["enabled"] = enabled
    copied["judge"] = judge_config
    return copied


def _prepare_dataset(
    items: list[dict[str, Any]],
    *,
    judge_enabled: bool,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in items:
        gold = item.get("gold")
        gold_dict = dict(gold) if isinstance(gold, dict) else {"value": gold}
        prepared.append(
            {
                **item,
                "gold": _copy_gold_with_judge(gold_dict, enabled=judge_enabled),
            }
        )
    return prepared


def _noop_optimizer(text: str, failures: list[dict[str, Any]]) -> str:
    del failures
    return text


def run_optimize(
    *,
    inbox_root: Path,
    reference_root: Path,
    skill_path: Path,
    record_path: Path,
    now: str,
    make_rollout: HookFactory,
    make_score: HookFactory,
    make_optimizer: HookFactory,
    config: atomizer_config.AtomizerConfig | None = None,
    skillopt_config: SkillOptConfig | None = None,
    budget: int = 1,
    dry_run: bool = False,
) -> int:
    resolved_skillopt = skillopt_config or _default_skillopt_config(config)
    datasets = build_valset(
        inbox_root=Path(inbox_root),
        reference_root=Path(reference_root),
        config=config,
        val_ratio=resolved_skillopt.val_ratio,
        min_project_sample=resolved_skillopt.min_project_sample,
    )
    if not datasets["val"]:
        print(
            "skillopt: no validation items available yet. Run importer first to "
            "populate the inbox; if the inbox already exists, the current split "
            "settings may still leave everything in train (for example "
            "min_project_sample/val_ratio)."
        )
        return 2

    train_set = _prepare_dataset(datasets["train"], judge_enabled=False)
    val_set = _prepare_dataset(datasets["val"], judge_enabled=True)
    optimizer = _noop_optimizer if dry_run else make_optimizer()

    try:
        result = optimize_skill(
            Path(skill_path),
            rollout=make_rollout(),
            score=make_score(),
            train_set=train_set,
            val_set=val_set,
            optimizer=optimizer,
            budget=0 if dry_run else budget,
            now=now,
            record_path=Path(record_path),
        )
    except SkillOptError as exc:
        print(f"skillopt: cannot optimize: {exc}")
        return 2

    payload = dict(result)
    payload.update(
        {
            "dry_run": dry_run,
            "train_items": len(train_set),
            "val_items": len(val_set),
        }
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


def _default_memory_root() -> Path:
    return paths.memory_root()


def _default_reference_root() -> Path:
    return paths.notes_root()


def _resolve_skill_path(args: argparse.Namespace, config: atomizer_config.AtomizerConfig) -> Path:
    if args.skill_path:
        return Path(args.skill_path).expanduser()
    return atomizer_cli._resolve_skill_path(config)


def _build_default_hooks(
    config: atomizer_config.AtomizerConfig,
    skillopt_config: SkillOptConfig,
) -> tuple[HookFactory, HookFactory, HookFactory]:
    known_projects = atomizer_cli._known_projects(config.known_projects_file)
    judge_command = list(skillopt_config.judge_command)

    # SkillOpt's judge is another external headless task.  Keep the normal
    # configured profile set (and its tier fallback); only an explicit
    # skillopt.yaml command becomes a constrained Tier-3 custom profile.  It
    # still goes through the same stdin/minimal-env/safety contract.
    if tuple(judge_command) == tuple(config.agent_exec_command):
        judge_profiles = config.external_profiles
    else:
        judge_profiles = (
            AgentProfile.from_mapping(
                {
                    "id": "skillopt-custom",
                    "tier": 3,
                    "priority": 0,
                    "traits": ["custom-judge"],
                    "task_classes": ["skillopt"],
                    "model": config.agent_exec_model,
                    "effort": "medium",
                    "supported_efforts": ["medium"],
                    "argv": judge_command,
                }
            ),
        )

    def make_rollout():
        agent = ExternalAgentRouter(
            config.external_profiles,
            task_class="atomization",
            deadline_seconds=config.router_deadline_seconds,
            max_attempts=config.router_max_attempts,
            max_agent_calls=config.router_max_agent_calls,
        )
        return make_atomize_rollout(agent, known_projects, config=config)

    def make_score():
        judge = ExternalAgentRouter(
            judge_profiles,
            task_class="skillopt",
            deadline_seconds=min(
                max(int(skillopt_config.judge_timeout), 1),
                FIXED_TIMEOUT_SECONDS,
            ),
        )
        return make_hybrid_score(judge, alpha=skillopt_config.alpha)

    def make_optimizer():
        return make_acp_optimizer()

    return make_rollout, make_score, make_optimizer


def run(args: argparse.Namespace) -> int:
    try:
        config, _ = atomizer_config.load_config()
    except atomizer_config.AtomizerConfigError as exc:
        print(f"skillopt: cannot load atomizer config: {exc}")
        return 2
    try:
        skillopt_config = load_skillopt_config(atomizer_cfg=config)
    except SkillOptConfigError as exc:
        print(f"skillopt: cannot load skillopt config: {exc}")
        return 2
    memory_root = Path(args.memory_root).expanduser()
    now = args.now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    make_rollout, make_score, make_optimizer = _build_default_hooks(config, skillopt_config)
    return run_optimize(
        inbox_root=memory_root / "inbox",
        reference_root=Path(args.reference_root).expanduser(),
        skill_path=_resolve_skill_path(args, config),
        record_path=memory_root / "runtime" / "ledger" / "skillopt.jsonl",
        now=now,
        make_rollout=make_rollout,
        make_score=make_score,
        make_optimizer=make_optimizer,
        config=config,
        skillopt_config=skillopt_config,
        budget=args.budget,
        dry_run=args.dry_run,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psc memory skillopt")
    subparsers = parser.add_subparsers(dest="skillopt_command", required=True)
    run_parser = subparsers.add_parser("run", help="optimize the atomize skill")
    run_parser.add_argument("--memory-root", default=str(_default_memory_root()))
    run_parser.add_argument("--reference-root", default=str(_default_reference_root()))
    run_parser.add_argument("--skill-path", default=None)
    run_parser.add_argument("--budget", type=int, default=1)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--now", default=None)
    run_parser.set_defaults(func=run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
