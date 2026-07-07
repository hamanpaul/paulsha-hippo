"""
Janitor configuration loader and hash.
"""
import hashlib
import json
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paulsha_hippo import paths


class JanitorConfigError(Exception):
    """Configuration error in janitor subsystem."""
    pass


@dataclass
class JanitorConfig:
    """Janitor configuration dataclass."""
    schema_version: str
    default_decay_age_days: int
    by_artifact_kind: dict[str, int]
    check_provenance_path: bool
    check_provenance_commit: bool
    decay_superseded: bool


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent
_SUPPORTED_SCHEMA_VERSION = "1"
_DEFAULT_SENTINEL = object()


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, returning new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict:
    """Load YAML file, fallback to JSON if PyYAML unavailable."""
    try:
        import yaml
    except ImportError:
        # Fallback to JSON - lifecycle.yaml is valid YAML but we need yaml for this
        raise JanitorConfigError(
            f"PyYAML not available and {path.name} requires YAML parsing"
        )

    try:
        with path.open('r') as f:
            return yaml.safe_load(f)
    except JanitorConfigError:
        raise
    except OSError as exc:
        raise JanitorConfigError(f"Cannot read config file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise JanitorConfigError(f"Invalid YAML in config file {path}: {exc}") from exc


def _compute_hash(config_dict: dict) -> str:
    """Compute deterministic sha256 hash of config."""
    canonical = json.dumps(config_dict, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _parse_config(merged: dict) -> JanitorConfig:
    """Parse merged config dict into JanitorConfig."""
    try:
        schema_version = str(merged.get("schema_version", ""))
        
        ttl = merged.get("ttl")
        if not isinstance(ttl, dict):
            raise JanitorConfigError("ttl must be a dict")
        
        default_decay_age_days = ttl.get("default_decay_age_days")
        if isinstance(default_decay_age_days, bool) or not isinstance(default_decay_age_days, int):
            raise JanitorConfigError("default_decay_age_days must be an int")
        if default_decay_age_days <= 0:
            raise JanitorConfigError("default_decay_age_days must be positive")
        
        by_artifact_kind = ttl.get("by_artifact_kind", {})
        if not isinstance(by_artifact_kind, dict):
            raise JanitorConfigError("by_artifact_kind must be a dict")
        for artifact_kind, days in by_artifact_kind.items():
            if isinstance(days, bool) or not isinstance(days, int):
                raise JanitorConfigError(
                    f"by_artifact_kind[{artifact_kind!r}] must be an int"
                )
            if days <= 0:
                raise JanitorConfigError(
                    f"by_artifact_kind[{artifact_kind!r}] must be positive"
                )
        
        source_checks = merged.get("source_checks")
        if not isinstance(source_checks, dict):
            raise JanitorConfigError("source_checks must be a dict")
        
        check_provenance_path = source_checks.get("check_provenance_path")
        if not isinstance(check_provenance_path, bool):
            raise JanitorConfigError("check_provenance_path must be a bool")
        
        check_provenance_commit = source_checks.get("check_provenance_commit")
        if not isinstance(check_provenance_commit, bool):
            raise JanitorConfigError("check_provenance_commit must be a bool")
        
        supersede = merged.get("supersede")
        if not isinstance(supersede, dict):
            raise JanitorConfigError("supersede must be a dict")
        
        decay_superseded = supersede.get("decay_superseded")
        if not isinstance(decay_superseded, bool):
            raise JanitorConfigError("decay_superseded must be a bool")
        
        return JanitorConfig(
            schema_version=schema_version,
            default_decay_age_days=default_decay_age_days,
            by_artifact_kind=by_artifact_kind,
            check_provenance_path=check_provenance_path,
            check_provenance_commit=check_provenance_commit,
            decay_superseded=decay_superseded,
        )
    except (KeyError, TypeError, AttributeError) as e:
        raise JanitorConfigError(f"Invalid config structure: {e}")


def load_config(
    default_dir: str | Path | None = None,
    override_path: str | Path | None | object = _DEFAULT_SENTINEL
) -> tuple[JanitorConfig, str]:
    """
    Load janitor configuration with optional override.
    
    Args:
        default_dir: Directory containing lifecycle.yaml (default: janitor module dir)
        override_path: Override config path. If sentinel (default), checks
            ~/.config/paulshaclaw/janitor.override.yaml if it exists.
            If None, no override is used.
    
    Returns:
        Tuple of (JanitorConfig, config_hash)
    
    Raises:
        JanitorConfigError: On unsupported schema version or invalid config
    """
    if default_dir is None:
        default_dir = DEFAULT_CONFIG_DIR
    else:
        default_dir = Path(default_dir)
    
    # Load base config
    lifecycle_path = default_dir / "lifecycle.yaml"
    if not lifecycle_path.exists():
        raise JanitorConfigError(f"lifecycle.yaml not found at {lifecycle_path}")
    
    base_config = _load_yaml(lifecycle_path)
    
    # Determine override path
    if override_path is _DEFAULT_SENTINEL:
        override_path = paths.config_path("janitor.override.yaml")
        if not override_path.exists():
            override_path = None
    elif override_path is not None:
        override_path = Path(override_path)
    
    # Merge with override if present
    if override_path is not None:
        override_config = _load_yaml(override_path)
        if override_config is None:
            override_config = {}
        elif not isinstance(override_config, dict):
            raise JanitorConfigError(
                f"Override config must be a dict, got {type(override_config).__name__}"
            )
        merged = _deep_merge(base_config, override_config)
    else:
        merged = base_config
    
    # Check schema version
    schema_version = str(merged.get("schema_version", ""))
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise JanitorConfigError(
            f"Unsupported schema_version: {schema_version}. "
            f"Supported: {_SUPPORTED_SCHEMA_VERSION}"
        )
    
    # Parse config
    config = _parse_config(merged)
    
    # Compute hash
    config_hash = _compute_hash(merged)
    
    return config, config_hash
