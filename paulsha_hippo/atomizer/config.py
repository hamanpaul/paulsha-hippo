"""Atomizer configuration loader with deterministic hashing."""
import copy
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from paulsha_hippo import paths

# Default config directory is package location
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_EXEC_COMMAND = (
    "/bin/bash",
    "-c",
    'set -eu; export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v22.20.0/bin:$PATH"; prompt=$(cat); exec "$HOME/.local/bin/co-gem" -s --no-color --available-tools=none --disable-builtin-mcps --no-custom-instructions --no-ask-user --no-remote --no-remote-export -p "$prompt"',
)
DEFAULT_AGENT_EXEC_TIMEOUT = 300
DEFAULT_AGENT_EXEC_MODEL = "gem"
DEFAULT_AGENT_EXEC_MAX_OUTPUT_TOKENS = 2048
DEFAULT_AGENT_EXEC_UPSTREAM_URL = "http://127.0.0.1:8080"
DEFAULT_CONTEXT_WINDOW = 32768
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
    agent_exec_upstream_url: str = DEFAULT_AGENT_EXEC_UPSTREAM_URL
    agent_exec_backend: str = "custom-argv"
    agent_exec_base_url: str = ""
    agent_exec_api_key_env: str = ""
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
    env_upstream = os.environ.get("PSC_CLAUDE_GEMMA4_UPSTREAM_URL")
    try:
        cfg, _ = load_config()
        command = resolve_command_argv(cfg.agent_exec_command)
        upstream = cfg.agent_exec_upstream_url
    except Exception:
        command = resolve_command_argv(DEFAULT_AGENT_EXEC_COMMAND)
        upstream = DEFAULT_AGENT_EXEC_UPSTREAM_URL
    if env_upstream is not None:
        return command, env_upstream
    return command, upstream


def build_agent_exec_env(
    *,
    upstream_url: str,
    max_output_tokens: int | None = None,
) -> dict[str, str]:
    env = {"PSC_CLAUDE_GEMMA4_UPSTREAM_URL": upstream_url}
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
    # Resolve default config directory
    if default_dir is None:
        config_dir = DEFAULT_CONFIG_DIR
    else:
        config_dir = Path(default_dir)
    
    # Load default config
    default_config_path = config_dir / "atomizer.yaml"
    if not default_config_path.exists():
        raise AtomizerConfigError(f"Default config not found: {default_config_path}")
    
    config_data = dict(_read_mapping(default_config_path))
    
    # Merge override if path resolves and exists
    resolved_override = _resolve_override(override_path)
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
    agent_exec_upstream_url = _require_non_empty_string(
        agent_exec_config.get(
            "upstream_url",
            os.environ.get(
                "PSC_CLAUDE_GEMMA4_UPSTREAM_URL",
                DEFAULT_AGENT_EXEC_UPSTREAM_URL,
            ),
        ),
        "agent_exec.upstream_url",
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
        "context_window": (context_window, DEFAULT_CONTEXT_WINDOW),
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
    
    agent_exec_backend = str(agent_exec_config.get("backend", "custom-argv"))
    if agent_exec_backend not in ("custom-argv", "openai-compatible", "claude-headless"):
        raise AtomizerConfigError(f"Unsupported agent_exec.backend: {agent_exec_backend}")
    agent_exec_base_url = str(agent_exec_config.get("base_url", "") or "")
    agent_exec_api_key_env = str(agent_exec_config.get("api_key_env", "") or "")
    if agent_exec_backend == "openai-compatible" and not agent_exec_base_url:
        raise AtomizerConfigError("agent_exec.backend=openai-compatible requires agent_exec.base_url")

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
        agent_exec_upstream_url=agent_exec_upstream_url,
        agent_exec_backend=agent_exec_backend,
        agent_exec_base_url=agent_exec_base_url,
        agent_exec_api_key_env=agent_exec_api_key_env,
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
