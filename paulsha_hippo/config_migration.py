"""Hash-bound, fail-closed migration from legacy distiller configuration.

The migration planner is intentionally separate from normal runtime loading:
runtime reads one canonical config, while this module may inspect legacy input
to produce a reviewable plan.  It never copies a prohibited provider value into
an error, backup, manifest, or log.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


PROHIBITED_KEYS = frozenset(
    {
        "api_key",
        "api-key",
        "api_key_env",
        "api-key-env",
        "base_url",
        "base-url",
        "credential_env",
        "credential-env",
        "credential_store",
        "credential-store",
        "oauth",
        "oauth_state",
        "oauth-state",
        "provider_url",
        "provider-url",
        "secret_path",
        "secret-path",
        "upstream_url",
        "upstream-url",
    }
)


class MigrationError(ValueError):
    """A migration cannot be safely planned or applied."""


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_document(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, _hash_bytes(b"")
    except OSError as exc:
        raise MigrationError(f"cannot read config source: {path.name}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(f"config source is not UTF-8: {path.name}") from exc
    try:
        import yaml

        value = yaml.safe_load(text) or {}
    except ImportError as exc:
        raise MigrationError("PyYAML is required for config migration") from exc
    except Exception as exc:
        raise MigrationError(f"config source is invalid: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise MigrationError(f"config source root is not a mapping: {path.name}")
    return dict(value), _hash_bytes(raw)


def _nonempty(value: object) -> bool:
    return value not in (None, "", [], {}, ())


def _prohibited_fields(value: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in PROHIBITED_KEYS and _nonempty(child):
                found.append(child_path)
            found.extend(_prohibited_fields(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            found.extend(_prohibited_fields(child, f"{path}[{index}]"))
    return sorted(set(found))


def _semantic(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _semantic_diff(left: Mapping[str, Any], right: Mapping[str, Any], path: str = "") -> list[str]:
    keys = sorted(set(left) | set(right), key=str)
    result: list[str] = []
    for key in keys:
        key_text = str(key)
        current = f"{path}.{key_text}" if path else key_text
        if key not in left or key not in right:
            result.append(current)
            continue
        a, b = left[key], right[key]
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            result.extend(_semantic_diff(a, b, current))
        elif a != b:
            result.append(current)
    return result


@dataclass(frozen=True)
class MigrationPlan:
    canonical_path: str
    legacy_path: str | None
    canonical_hash: str
    legacy_hash: str
    status: str
    reason: str | None
    prohibited_fields: tuple[str, ...]
    conflicts: tuple[str, ...]
    selected_source: str | None
    plan_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "canonical_path": self.canonical_path,
            "legacy_path": self.legacy_path,
            "canonical_hash": self.canonical_hash,
            "legacy_hash": self.legacy_hash,
            "status": self.status,
            "reason": self.reason,
            "prohibited_fields": list(self.prohibited_fields),
            "conflicts": list(self.conflicts),
            "selected_source": self.selected_source,
            "plan_hash": self.plan_hash,
        }


def _with_hash(payload: dict[str, Any]) -> MigrationPlan:
    body = dict(payload)
    body.pop("plan_hash", None)
    plan_hash = _hash_bytes(_semantic(body).encode("utf-8"))
    return MigrationPlan(plan_hash=plan_hash, **body)


def plan_migration(
    canonical_path: str | Path,
    legacy_path: str | Path | None = None,
) -> MigrationPlan:
    canonical = Path(canonical_path).expanduser()
    legacy = Path(legacy_path).expanduser() if legacy_path is not None else None
    canonical_data, canonical_hash = _read_document(canonical)
    legacy_data, legacy_hash = ({}, _hash_bytes(b"")) if legacy is None else _read_document(legacy)
    prohibited = tuple(sorted(set(_prohibited_fields(canonical_data) + _prohibited_fields(legacy_data))))
    conflicts = tuple(_semantic_diff(canonical_data, legacy_data)) if canonical_data and legacy_data else ()
    if prohibited:
        return _with_hash(
            {
                "canonical_path": str(canonical),
                "legacy_path": str(legacy) if legacy else None,
                "canonical_hash": canonical_hash,
                "legacy_hash": legacy_hash,
                "status": "blocked",
                "reason": "operator-redaction-required",
                "prohibited_fields": prohibited,
                "conflicts": conflicts,
                "selected_source": None,
            }
        )
    if canonical_data and legacy_data and conflicts:
        status, reason, selected = "conflict", "hash-bound-resolution-required", None
    elif canonical_data:
        status, reason, selected = "ready", None, "canonical"
    elif legacy_data:
        status, reason, selected = "ready", None, "legacy"
    else:
        status, reason, selected = "ready", "empty-canonical", "canonical"
    return _with_hash(
        {
            "canonical_path": str(canonical),
            "legacy_path": str(legacy) if legacy else None,
            "canonical_hash": canonical_hash,
            "legacy_hash": legacy_hash,
            "status": status,
            "reason": reason,
            "prohibited_fields": (),
            "conflicts": conflicts,
            "selected_source": selected,
        }
    )


def _load_plan(value: MigrationPlan | Mapping[str, Any] | str | Path) -> MigrationPlan:
    if isinstance(value, MigrationPlan):
        return value
    if isinstance(value, (str, Path)):
        try:
            payload = json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError("invalid migration plan") from exc
    else:
        payload = dict(value)
    payload.pop("schema_version", None)
    expected = payload.pop("plan_hash", None)
    rebuilt = _with_hash(payload)
    if expected != rebuilt.plan_hash:
        raise MigrationError("migration plan hash mismatch")
    return rebuilt


def _resolution_source(
    plan: MigrationPlan,
    resolution: Mapping[str, Any] | str | Path | None,
) -> str:
    if plan.status == "blocked":
        raise MigrationError("operator-redaction-required")
    if plan.status == "conflict":
        if resolution is None:
            raise MigrationError("hash-bound-resolution-required")
        if isinstance(resolution, (str, Path)):
            try:
                value = json.loads(Path(resolution).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MigrationError("invalid resolution file") from exc
        else:
            value = dict(resolution)
        if value.get("plan_hash") != plan.plan_hash:
            raise MigrationError("resolution plan hash mismatch")
        if value.get("canonical_hash") != plan.canonical_hash or value.get("legacy_hash") != plan.legacy_hash:
            raise MigrationError("resolution source drift")
        source = value.get("selected_source")
        if source not in {"canonical", "legacy"}:
            raise MigrationError("resolution must select canonical or legacy")
        return str(source)
    return plan.selected_source or "canonical"


def apply_migration(
    plan: MigrationPlan | Mapping[str, Any] | str | Path,
    *,
    resolution: Mapping[str, Any] | str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply only a reviewed, hash-stable plan; return a redacted report."""
    current = _load_plan(plan)
    source = _resolution_source(current, resolution)
    canonical = Path(current.canonical_path)
    legacy = Path(current.legacy_path) if current.legacy_path else None
    source_path = canonical if source == "canonical" else legacy
    if source_path is None:
        raise MigrationError("selected legacy source is missing")
    source_data, source_hash = _read_document(source_path)
    expected_hash = current.canonical_hash if source == "canonical" else current.legacy_hash
    if source_hash != expected_hash:
        raise MigrationError("migration source drift")
    if _prohibited_fields(source_data):
        raise MigrationError("operator-redaction-required")
    report: dict[str, Any] = {
        "status": "dry-run" if dry_run else "applied",
        "source": source,
        "canonical_path": str(canonical),
        "source_hash": source_hash,
        "plan_hash": current.plan_hash,
        "semantic_hash": _hash_bytes(_semantic(source_data).encode("utf-8")),
    }
    if dry_run:
        return report
    canonical.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        rendered = yaml.safe_dump(copy.deepcopy(source_data), sort_keys=False, allow_unicode=True)
    except ImportError as exc:
        raise MigrationError("PyYAML is required for config migration") from exc
    fd, tmp_name = tempfile.mkstemp(prefix=".hippo-config-", dir=str(canonical.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, canonical)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return report


__all__ = [
    "MigrationError",
    "MigrationPlan",
    "PROHIBITED_KEYS",
    "apply_migration",
    "plan_migration",
]
