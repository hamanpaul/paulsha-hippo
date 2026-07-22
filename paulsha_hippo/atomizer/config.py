"""Atomizer configuration loader with deterministic hashing."""
import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from paulsha_hippo import paths
from paulsha_hippo.agent_profiles import (
    AgentProfile,
    FIXED_MAX_AGENT_CALLS,
    FIXED_MAX_ATTEMPTS,
    FIXED_TIMEOUT_SECONDS,
    ProfileConfigError,
    default_profiles,
    profiles_from_config,
)

from .limits import MIN_CONTEXT_WINDOW

# Default config directory is package location
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_EXEC_COMMAND = (
    "co-gem", "--model", "{MODEL}", "--effort", "{EFFORT}", "--headless", "--stdin",
)
DEFAULT_AGENT_EXEC_TIMEOUT = 300
DEFAULT_AGENT_EXEC_MODEL = "local"
DEFAULT_AGENT_EXEC_MAX_OUTPUT_TOKENS = 2048
DEFAULT_CONTEXT_WINDOW = MIN_CONTEXT_WINDOW
DEFAULT_MAX_INPUT_TOKENS = 12000
DEFAULT_MAX_PROMPT_ARGV_BYTES = 48 * 1024
DEFAULT_CHUNK_RETRIES = 2
DEFAULT_PARALLELISM = 1

# Supported schema version
_SUPPORTED_SCHEMA = "1"

# Sentinel for default override path resolution
_DEFAULT_SENTINEL = object()


class AtomizerConfigError(Exception):
    """Raised when atomizer configuration is invalid or unsupported."""
    pass


@dataclass(frozen=True)
class AtomizerConfig:
    """Atomizer configuration."""
    schema_version: str
    boundary_patterns: tuple[str, ...]
    max_fragment_chars: int
    artifact_kind_map: Mapping[str, str]
    phase_map: Mapping[str, str]
    default_artifact_kind: str = "report"
    default_phase: str = "review"
    agent_exec_command: tuple[str, ...] = DEFAULT_AGENT_EXEC_COMMAND
    agent_exec_timeout: int = DEFAULT_AGENT_EXEC_TIMEOUT
    agent_exec_model: str = DEFAULT_AGENT_EXEC_MODEL
    agent_exec_max_output_tokens: int = DEFAULT_AGENT_EXEC_MAX_OUTPUT_TOKENS
    agent_exec_backend: str = "external-cli"
    external_profiles: tuple[AgentProfile, ...] = field(default_factory=default_profiles)
    router_deadline_seconds: int = FIXED_TIMEOUT_SECONDS
    router_max_attempts: int = FIXED_MAX_ATTEMPTS
    router_max_agent_calls: int = FIXED_MAX_AGENT_CALLS
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    max_prompt_argv_bytes: int = DEFAULT_MAX_PROMPT_ARGV_BYTES
    chunk_retries: int = DEFAULT_CHUNK_RETRIES
    parallelism: int = DEFAULT_PARALLELISM
    default_promoter: str = "identity"
    skill_path: str = "skills/atomize-knowledge-slice.md"
    known_projects_file: str = field(default_factory=lambda: str(paths.projects_config_path()))


def _read_mapping(path: Path) -> Mapping[str, Any]:
    """Read YAML or JSON config file and return root mapping.
    
    Args:
        path: Path to config file
        
    Returns:
        Mapping from config file root
        
    Raises:
        AtomizerConfigError: If file cannot be read or root is not a mapping
    """
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as e:
        raise AtomizerConfigError(f"Cannot read config file {path}: {e}") from e
    
    # Try YAML first if available, fall back to JSON
    try:
        import yaml
        try:
            data = yaml.safe_load(text)
        except Exception as e:
            raise AtomizerConfigError(f"Cannot parse YAML from {path}: {e}") from e
    except ImportError as exc:
        if path.suffix.lower() in {".yaml", ".yml"}:
            raise AtomizerConfigError(
                f"Cannot parse YAML from {path}: PyYAML dependency is not installed"
            ) from exc
        try:
            data = json.loads(text)
        except Exception as e:
            raise AtomizerConfigError(f"Cannot parse JSON from {path}: {e}") from e
    
    if not isinstance(data, Mapping):
        raise AtomizerConfigError(f"Config file {path} root must be a mapping, got {type(data).__name__}")
    
    return data


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep merge override into base, preserving base defaults.
    
    Args:
        base: Base dictionary (will be copied, not modified)
        override: Override mapping to merge in
        
    Returns:
        New merged dictionary
    """
    result = copy.deepcopy(base)
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, Mapping):
            # Recursively merge nested dicts
            result[key] = _deep_merge(result[key], value)
        else:
            # Override scalar or new key
            result[key] = value
    
    return result


def _resolve_override(override_path):
    """Resolve override path from parameter.
    
    Args:
        override_path: None (disabled), _DEFAULT_SENTINEL (use default), or explicit path
        
    Returns:
        Resolved Path or None if disabled
    """
    if override_path is None:
        return None
    elif override_path is _DEFAULT_SENTINEL:
        return paths.config_path("atomizer.override.yaml")
    else:
        return Path(override_path)


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AtomizerConfigError(f"{field_name} must be non-empty string")
    return value


def _parse_agent_exec_command(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list):
        raise AtomizerConfigError("agent_exec.command must be list")
    if not value:
        raise AtomizerConfigError("agent_exec.command must not be empty")
    command: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise AtomizerConfigError(f"agent_exec.command[{index}] must be non-empty string")
        command.append(item)
    return tuple(command)


def _parse_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise AtomizerConfigError(f"{field_name} must be int, got bool")
    if isinstance(value, float):
        raise AtomizerConfigError(f"{field_name} must be int, got float")
    if not isinstance(value, (int, str)):
        raise AtomizerConfigError(f"{field_name} must be a positive int")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AtomizerConfigError(f"{field_name} must be a positive int") from exc
    if parsed <= 0:
        raise AtomizerConfigError(f"{field_name} must be positive, got {parsed}")
    return parsed


def is_safe_path_component(value: str) -> bool:
    return (
        value.strip() == value
        and value not in {"", ".", ".."}
        and "/" not in value
        and "\\" not in value
        and "*" not in value
        and "?" not in value
        and "[" not in value
        and "]" not in value
        and "\x00" not in value
    )


def is_valid_project_id(value: str) -> bool:
    """Validate rich project metadata without imposing filesystem-token rules."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        return False
    if value.startswith(("/", "~")) or any(ord(char) < 32 for char in value):
        return False
    segments = value.replace("\\", "/").split("/")
    return all(segment not in {"", ".", ".."} for segment in segments)


def sanitize_project_component(value: str) -> str:
    """Map any project identifier (including URL form with '/') to a path-safe
    component. The original rich value should be preserved separately in metadata;
    this is only for filesystem directory naming under the knowledge and slice layers."""
    text = (value or "").strip().replace("\\", "/")
    text = text.strip("/").replace("..", "__")
    text = text.replace("/", "__")
    text = "".join(ch for ch in text if ch not in "*?[]\x00")
    return text or "_unknown"


def project_directory_key(value: str, *, hash_length: int = 12) -> str:
    """Return a readable, collision-resistant filesystem key for a rich ID.

    The canonical project value remains untouched in frontmatter and ledgers.
    Simple legacy identifiers keep their old directory names; rich identifiers
    receive a stable ``--p-<hash>`` suffix so URL/path sanitization collisions
    cannot merge two projects on disk.
    """
    canonical = str(value or "").strip()
    if not canonical:
        return "_unknown"
    if re.fullmatch(r"[A-Za-z0-9._-]+", canonical):
        return sanitize_project_component(canonical)
    prefix = sanitize_project_component(canonical)
    prefix = re.sub(r"_+", "-", prefix).strip("-_") or "project"
    prefix = prefix[:64].rstrip("-_") or "project"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:hash_length]
    return f"{prefix}--p-{digest}"


def resolve_command_argv(
    command: Sequence[str], *, base_dir: str | Path = PROJECT_ROOT
) -> tuple[str, ...]:
    root = Path(base_dir)
    resolved: list[str] = []
    for token in command:
        candidate = Path(token).expanduser()
        if candidate.is_absolute():
            resolved.append(str(candidate))
            continue
        rooted_candidate = root / candidate
        if candidate.parts and rooted_candidate.exists():
            resolved.append(str(rooted_candidate))
            continue
        resolved.append(token)
    return tuple(resolved)


def resolve_agent_exec_settings() -> tuple[tuple[str, ...], str]:
    """Return the first configured external CLI command and its model.

    The second value is deliberately a model label, not an endpoint.  This
    compatibility-shaped helper has no provider URL or credential lookup and
    is retained for importer callers that need a command tuple.
    """
    try:
        cfg, _ = load_config()
        command = resolve_command_argv(cfg.agent_exec_command)
        model = cfg.agent_exec_model
        if cfg.external_profiles:
            command = cfg.external_profiles[0].render_argv()
            model = cfg.external_profiles[0].model
    except Exception:
        command = resolve_command_argv(DEFAULT_AGENT_EXEC_COMMAND)
        model = DEFAULT_AGENT_EXEC_MODEL
    return command, model


def build_agent_exec_env(
    *,
    max_output_tokens: int | None = None,
) -> dict[str, str]:
    # Kept as a compatibility helper for callers that construct a launcher env.
    # Hippo never resolves or forwards provider URLs; only the fixed output cap
    # is a non-secret value that may cross the child-process boundary.
    env: dict[str, str] = {}
    if max_output_tokens is not None:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    return env


def load_config(
    default_dir: str | Path | None = None,
    override_path: str | Path | None | object = _DEFAULT_SENTINEL
) -> tuple[AtomizerConfig, str]:
    """Load atomizer configuration with optional override and compute hash.
    
    Args:
        default_dir: Directory containing atomizer.yaml (default: package dir)
        override_path: Override config path or None to disable or _DEFAULT_SENTINEL for default
        
    Returns:
        Tuple of (AtomizerConfig, hex hash string)
        
    Raises:
        AtomizerConfigError: If config is invalid or schema unsupported
    """
    # Resolve the default config source.  The managed runtime config has a
    # different filename from the package template, so keep the full path
    # instead of resolving a directory and appending ``atomizer.yaml``.
    using_canonical_runtime_config = False
    if default_dir is None:
        canonical_path = paths.atomizer_config_path()
        if canonical_path.is_file():
            default_config_path = canonical_path
            using_canonical_runtime_config = True
        else:
            default_config_path = DEFAULT_CONFIG_DIR / "atomizer.yaml"
    else:
        default_config_path = Path(default_dir) / "atomizer.yaml"
    
    # Load default config
    if not default_config_path.exists():
        raise AtomizerConfigError(f"Default config not found: {default_config_path}")
    
    config_data = dict(_read_mapping(default_config_path))
    
    # Merge override if path resolves and exists
    # Once the managed canonical file exists, the runtime must not silently
    # merge the legacy provider/override surface.  Explicit override paths stay
    # available for isolated tests and operator-reviewed dry runs.
    resolved_override = (
        None if using_canonical_runtime_config and override_path is _DEFAULT_SENTINEL
        else _resolve_override(override_path)
    )
    if resolved_override is not None and resolved_override.exists():
        override_data = _read_mapping(resolved_override)
        config_data = _deep_merge(config_data, override_data)
    
    # Validate schema version
    schema_version = str(config_data.get("schema_version", ""))
    if schema_version != _SUPPORTED_SCHEMA:
        raise AtomizerConfigError(
            f"Unsupported schema version: {schema_version}. "
            f"Expected: {_SUPPORTED_SCHEMA}"
        )
    
    # Extract split configuration
    split_config = config_data.get("split", {})
    if not isinstance(split_config, Mapping):
        raise AtomizerConfigError(
            f"split must be a mapping, got {type(split_config).__name__}"
        )

    raw_patterns = split_config.get("boundary_patterns", [])
    if isinstance(raw_patterns, str) or not isinstance(raw_patterns, list):
        raise AtomizerConfigError(
            f"boundary_patterns must be list, got {type(raw_patterns).__name__}"
        )
    if not all(isinstance(pattern, str) for pattern in raw_patterns):
        raise AtomizerConfigError("boundary_patterns entries must be strings")
    boundary_patterns = tuple(raw_patterns)

    raw_max_fragment_chars = split_config.get("max_fragment_chars", 8000)
    if isinstance(raw_max_fragment_chars, bool):
        raise AtomizerConfigError("max_fragment_chars must be int, got bool")
    max_fragment_chars = int(raw_max_fragment_chars)
    if max_fragment_chars <= 0:
        raise AtomizerConfigError(
            f"max_fragment_chars must be positive, got {max_fragment_chars}"
        )
    
    # Extract maps
    artifact_kind_map = MappingProxyType(dict(config_data.get("artifact_kind_map", {})))
    phase_map = MappingProxyType(dict(config_data.get("phase_map", {})))
    
    # Extract defaults
    default_artifact_kind = config_data.get("default_artifact_kind", "report")
    default_phase = config_data.get("default_phase", "review")

    agent_exec_config = config_data.get("agent_exec", {})
    if not isinstance(agent_exec_config, Mapping):
        raise AtomizerConfigError(
            f"agent_exec must be a mapping, got {type(agent_exec_config).__name__}"
        )
    agent_exec_command = _parse_agent_exec_command(
        agent_exec_config.get("command", list(DEFAULT_AGENT_EXEC_COMMAND))
    )
    agent_exec_timeout = _parse_positive_int(
        agent_exec_config.get("timeout_seconds", DEFAULT_AGENT_EXEC_TIMEOUT),
        "agent_exec.timeout_seconds",
    )
    agent_exec_model = _require_non_empty_string(
        agent_exec_config.get("model", DEFAULT_AGENT_EXEC_MODEL),
        "agent_exec.model",
    )
    agent_exec_max_output_tokens = _parse_positive_int(
        agent_exec_config.get(
            "max_output_tokens",
            DEFAULT_AGENT_EXEC_MAX_OUTPUT_TOKENS,
        ),
        "agent_exec.max_output_tokens",
    )
    default_promoter = _require_non_empty_string(
        config_data.get("promoter", "identity"),
        "promoter",
    )
    if default_promoter not in {"identity", "llm"}:
        raise AtomizerConfigError(f"promoter must be identity or llm, got {default_promoter}")
    skill_path = _require_non_empty_string(
        config_data.get("skill_path", "skills/atomize-knowledge-slice.md"),
        "skill_path",
    )
    known_projects_file = _require_non_empty_string(
        config_data.get("known_projects_file", str(paths.projects_config_path())),
        "known_projects_file",
    )

    context_window = _parse_positive_int(
        config_data.get("context_window", DEFAULT_CONTEXT_WINDOW), "context_window"
    )
    if context_window < MIN_CONTEXT_WINDOW:
        raise AtomizerConfigError(
            "context_window must be at least "
            f"{MIN_CONTEXT_WINDOW}, got {context_window}"
        )
    max_input_tokens = _parse_positive_int(
        config_data.get("max_input_tokens", DEFAULT_MAX_INPUT_TOKENS), "max_input_tokens"
    )
    max_prompt_argv_bytes = _parse_positive_int(
        config_data.get("max_prompt_argv_bytes", DEFAULT_MAX_PROMPT_ARGV_BYTES),
        "max_prompt_argv_bytes",
    )
    chunk_retries = _parse_positive_int(
        config_data.get("chunk_retries", DEFAULT_CHUNK_RETRIES), "chunk_retries"
    )
    parallelism = _parse_positive_int(
        config_data.get("parallelism", DEFAULT_PARALLELISM), "parallelism"
    )
    fixed_values = {
        "max_input_tokens": (max_input_tokens, DEFAULT_MAX_INPUT_TOKENS),
        "max_prompt_argv_bytes": (max_prompt_argv_bytes, DEFAULT_MAX_PROMPT_ARGV_BYTES),
        "chunk_retries": (chunk_retries, DEFAULT_CHUNK_RETRIES),
        "parallelism": (parallelism, DEFAULT_PARALLELISM),
        "agent_exec.timeout_seconds": (agent_exec_timeout, DEFAULT_AGENT_EXEC_TIMEOUT),
        "agent_exec.max_output_tokens": (
            agent_exec_max_output_tokens,
            DEFAULT_AGENT_EXEC_MAX_OUTPUT_TOKENS,
        ),
    }
    for field_name, (actual, expected) in fixed_values.items():
        if actual != expected:
            raise AtomizerConfigError(f"{field_name} is fixed at {expected}, got {actual}")
    
    agent_exec_backend = str(agent_exec_config.get("backend", "external-cli"))
    if agent_exec_backend in {"openai-compatible", "http", "tcp"}:
        raise AtomizerConfigError(
            "operator-redaction-required: retired direct-provider backend "
            "agent_exec.backend"
        )
    if agent_exec_backend not in ("custom-argv", "external-cli", "claude-headless"):
        raise AtomizerConfigError(f"Unsupported agent_exec.backend: {agent_exec_backend}")
    prohibited = []
    for field_name in (
        "base_url", "api_key_env", "upstream_url", "provider_url", "oauth",
        "oauth_state", "secret_path", "credential_env", "credential_store",
    ):
        value = agent_exec_config.get(field_name)
        if value not in (None, "", [], {}):
            prohibited.append(f"agent_exec.{field_name}")
    for field_name in (
        "base_url", "api_key_env", "upstream_url", "provider_url", "oauth",
        "oauth_state", "secret_path", "credential_env", "credential_store",
    ):
        value = config_data.get(field_name)
        if value not in (None, "", [], {}):
            prohibited.append(field_name)
    if config_data.get("distiller") not in (None, "", {}, []):
        prohibited.append("distiller")
    if prohibited:
        raise AtomizerConfigError(
            "operator-redaction-required: prohibited direct-provider fields: "
            + ", ".join(sorted(set(prohibited)))
        )

    external_agents = config_data.get("external_agents", {})
    if external_agents is None:
        external_agents = {}
    if not isinstance(external_agents, Mapping):
        raise AtomizerConfigError("external_agents must be a mapping")
    try:
        external_profiles = profiles_from_config(external_agents.get("profiles"))
        # A legacy ``agent_exec.command`` is still an external CLI surface, not
        # a provider backend.  Preserve it as an explicit Tier-3 custom-local
        # profile so old operator overrides continue to work while all runtime
        # calls still pass through the same router and safety validation.
        if agent_exec_command != DEFAULT_AGENT_EXEC_COMMAND:
            custom = AgentProfile.from_mapping(
                {
                    "id": "custom-local",
                    "tier": 3,
                    "priority": 1,
                    "traits": ["custom", "fallback"],
                    "task_classes": ["atomization", "title", "skillopt"],
                    "model": agent_exec_model,
                    "effort": "medium",
                    "supported_efforts": ["low", "medium", "high", "xhigh"],
                    "argv": list(agent_exec_command),
                }
            )
            # An explicit legacy command is an operator-selected custom
            # profile.  Do not unexpectedly launch unrelated installed CLIs
            # before it; the declared profile itself remains subject to the
            # same stdin/shell/env safety contract.
            external_profiles = (custom,)
    except ProfileConfigError as exc:
        raise AtomizerConfigError(str(exc)) from exc
    router_deadline = _parse_positive_int(
        external_agents.get("deadline_seconds", FIXED_TIMEOUT_SECONDS),
        "external_agents.deadline_seconds",
    )
    router_attempts = _parse_positive_int(
        external_agents.get("max_attempts", FIXED_MAX_ATTEMPTS),
        "external_agents.max_attempts",
    )
    router_calls = _parse_positive_int(
        external_agents.get("max_agent_calls", FIXED_MAX_AGENT_CALLS),
        "external_agents.max_agent_calls",
    )
    if router_deadline > FIXED_TIMEOUT_SECONDS or router_attempts > FIXED_MAX_ATTEMPTS or router_calls > FIXED_MAX_AGENT_CALLS:
        raise AtomizerConfigError(
            "external_agents budgets cannot exceed fixed bounded limits"
        )

    # Build config object
    cfg = AtomizerConfig(
        schema_version=schema_version,
        boundary_patterns=boundary_patterns,
        max_fragment_chars=max_fragment_chars,
        artifact_kind_map=artifact_kind_map,
        phase_map=phase_map,
        default_artifact_kind=default_artifact_kind,
        default_phase=default_phase,
        agent_exec_command=agent_exec_command,
        agent_exec_timeout=agent_exec_timeout,
        agent_exec_model=agent_exec_model,
        agent_exec_max_output_tokens=agent_exec_max_output_tokens,
        agent_exec_backend=agent_exec_backend,
        external_profiles=external_profiles,
        router_deadline_seconds=router_deadline,
        router_max_attempts=router_attempts,
        router_max_agent_calls=router_calls,
        context_window=context_window,
        max_input_tokens=max_input_tokens,
        max_prompt_argv_bytes=max_prompt_argv_bytes,
        chunk_retries=chunk_retries,
        parallelism=parallelism,
        default_promoter=default_promoter,
        skill_path=skill_path,
        known_projects_file=known_projects_file,
    )
    
    # Compute deterministic hash of effective config
    # Use canonical JSON representation
    canonical_json = json.dumps(config_data, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
    
    return cfg, config_hash
