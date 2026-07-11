from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Mapping
from pathlib import Path

from .agent_exec import AgentExecClient, HttpAgentClient, CachingAgentClient
from . import config as atomizer_config
from . import pipeline
from .llm_promoter import LLMPromoter
from .promoter import IdentityPromoter, Promoter
from ..ledger import processing


def _known_projects(path_str: str) -> list[str]:
    path = Path(path_str).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError):
        return []

    try:
        import yaml
    except ModuleNotFoundError:
        return []

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, Mapping):
        return []

    projects = data.get("projects")
    if isinstance(projects, Mapping):
        return [
            str(name)
            for name in projects.keys()
            if isinstance(name, str) and atomizer_config.is_safe_path_component(name)
        ]
    if isinstance(projects, list):
        names: list[str] = []
        for item in projects:
            if isinstance(item, str) and atomizer_config.is_safe_path_component(item):
                names.append(item)
            elif isinstance(item, Mapping):
                name = item.get("name")
                if isinstance(name, str) and atomizer_config.is_safe_path_component(name):
                    names.append(name)
        return names
    return []


def _resolve_skill_path(config: atomizer_config.AtomizerConfig) -> Path:
    path = Path(config.skill_path).expanduser()
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parent / path


def _cache_dir(memory_root: Path) -> Path:
    return memory_root / "runtime" / "cache" / "atomize"


def _build_promoter(
    args: argparse.Namespace,
    config: atomizer_config.AtomizerConfig,
    memory_root: Path,
) -> Promoter:
    promoter_name = args.promoter or config.default_promoter
    if promoter_name != "llm":
        return IdentityPromoter()

    command = (
        list(atomizer_config.resolve_command_argv(shlex.split(args.agent_command)))
        if args.agent_command is not None
        else list(atomizer_config.resolve_command_argv(config.agent_exec_command))
    )
    if config.agent_exec_backend == "openai-compatible" and args.agent_command is None:
        inner = HttpAgentClient(
            config.agent_exec_base_url,
            config.agent_exec_model,
            api_key_env=config.agent_exec_api_key_env or None,
            timeout=config.agent_exec_timeout,
            max_tokens=config.agent_exec_max_output_tokens,
        )
    else:
        inner = AgentExecClient(
            command,
            timeout=config.agent_exec_timeout,
            # Config is authoritative: it threads the selected backend upstream plus the
            # output-token cap into the launcher even when the parent env differs.
            env=atomizer_config.build_agent_exec_env(
                upstream_url=config.agent_exec_upstream_url,
                max_output_tokens=config.agent_exec_max_output_tokens,
            ),
        )
    cached_client = CachingAgentClient(
        inner,
        _cache_dir(memory_root),
    )
    skill_path = _resolve_skill_path(config)
    skill_text = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    return LLMPromoter(
        cached_client,
        skill_text,
        _known_projects(config.known_projects_file),
        model=config.agent_exec_model,
    )


def _instruction_corpus(args: argparse.Namespace):
    # opt-in: no roots -> inert (empty) corpus, doc-fragment dropping stays off
    from ..instruction_corpus import corpus_for_roots
    return corpus_for_roots(getattr(args, "instruction_root", None))


def prepare_pipeline_inputs(
    args: argparse.Namespace,
    memory_root: Path,
) -> tuple[atomizer_config.AtomizerConfig | None, str, Promoter | None, Exception | None]:
    """dream／直呼 atomize 共用初始化邊界（#15）：config 載入 + promoter 建構。

    spec「config 無效立即 parked」不分入口——初始化失敗不得以例外逃出失敗鏈，
    故本函式不拋，回傳 (config, config_hash, promoter, error)；error 非 None 時
    其餘為已取得的部分值（config_hash 供 park 證據引用，load_config 即失敗時
    為 ""）。
    """
    config: atomizer_config.AtomizerConfig | None = None
    config_hash = ""
    promoter: Promoter | None = None
    error: Exception | None = None
    try:
        override = args.override if getattr(args, "override", None) else atomizer_config._DEFAULT_SENTINEL
        config, config_hash = atomizer_config.load_config(override_path=override)
        promoter = _build_promoter(args, config, memory_root)
    except Exception as exc:  # noqa: BLE001 —初始化失敗需入 park 鏈並被記錄
        error = exc
    return config, config_hash, promoter, error


def park_init_failure(memory_root: Path, *, error: Exception, now: str,
                      config_hash: str, dry_run: bool) -> list[str]:
    """初始化失敗（分類 backend_unavailable）的共用 park 出口（dream＋直呼 atomize）。

    非 dry-run 時把 eligible（state == split）sessions 立即 park（含 `_failed/`
    證據）；dry-run 不落盤（park／證據皆為 mutation）。config_hash 取不到時記
    "unavailable" sentinel，證據仍可落。回傳被 park 的 session keys。
    """
    if dry_run:
        return []
    return pipeline.park_split_sessions(
        memory_root,
        error_text=str(error),
        now=now,
        config_hash=config_hash or "unavailable",
    )


def run(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)
    config, config_hash, promoter, init_error = prepare_pipeline_inputs(args, memory_root)
    if init_error is not None:
        # #15 失敗鏈：直呼 `hippo atomize` 與 dream 同一套初始化失敗邊界——
        # config 無效／promoter 建構失敗即 backend_unavailable，eligible split
        # sessions 立即 park（含證據），CLI 以結構化錯誤收斂（exit 1），
        # 不再讓例外 traceback 逃逸、session 卡在 split 且 `_failed/` 無證據。
        parked = park_init_failure(
            memory_root, error=init_error, now=args.now,
            config_hash=config_hash, dry_run=args.dry_run,
        )
        print(json.dumps({
            "error": type(init_error).__name__,
            "error_message": processing.sanitize_error_text(str(init_error)),
            "failure_category": "backend_unavailable",
            "parked": parked,
            "dry_run": bool(args.dry_run),
        }, sort_keys=True, indent=2))
        return 1
    result = pipeline.run(
        memory_root,
        config=config,
        config_hash=config_hash,
        now=args.now,
        dry_run=args.dry_run,
        promoter=promoter,
        doc_corpus=_instruction_corpus(args),
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0
