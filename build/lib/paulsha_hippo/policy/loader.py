from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from paulsha_hippo import paths

from .models import (
    BoundaryPolicy,
    ClassificationPolicy,
    EffectivePolicy,
    PolicyError,
    PolicyVersionError,
    ProjectDefault,
    SUPPORTED_POLICY_MAJOR,
    SecretRule,
)

DEFAULT_POLICY_DIR = Path(__file__).resolve().parent
_USE_DEFAULT_OVERRIDE = object()


def _read_mapping(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, Mapping):
        raise TypeError(f"policy file must contain a mapping: {path}")
    return data


def load_default_policy() -> EffectivePolicy:
    return load_policy(override_path=None)


def load_policy(
    default_dir: str | Path | None = None,
    override_path: str | Path | None | object = _USE_DEFAULT_OVERRIDE,
) -> EffectivePolicy:
    policy_dir = Path(default_dir) if default_dir is not None else DEFAULT_POLICY_DIR
    secrets = _read_mapping(policy_dir / "secrets.yaml")
    classification = _read_mapping(policy_dir / "classification.yaml")
    boundaries = _read_mapping(policy_dir / "boundaries.yaml")

    policy_version = str(secrets["policy_version"])
    _validate_major_version(policy_version)
    _validate_major_version(str(classification["policy_version"]))
    _validate_major_version(str(boundaries["policy_version"]))

    secret_rules = _secret_rules(secrets.get("rules", []))
    classification_policy = _classification_policy(classification)
    boundary_policies = _boundary_policies(boundaries)
    disabled_rules: frozenset[str] = frozenset()
    disabled_rules_for_session: dict[str, frozenset[str]] = {}

    override_file = _resolve_override_file(override_path)
    if override_file is not None and override_file.exists():
        override = _read_mapping(override_file)
        disabled_rules = frozenset(str(rule_id) for rule_id in override.get("disable_rules", []))
        disabled_rules_for_session = {
            str(session_ref): frozenset(str(rule_id) for rule_id in rule_ids)
            for session_ref, rule_ids in override.get("disable_rules_for_session", {}).items()
        }
        appended_rules = _secret_rules(override.get("append_regex_rules", []))
        duplicate_rule_ids = set(secret_rules).intersection(appended_rules)
        if duplicate_rule_ids:
            raise PolicyError(
                "append_regex_rules cannot replace existing rules: "
                + ", ".join(sorted(duplicate_rule_ids))
            )
        secret_rules.update(appended_rules)
        classification_policy = _merge_project_defaults(
            classification_policy,
            override.get("project_defaults", []),
        )

    return EffectivePolicy(
        policy_version=policy_version,
        secret_rules=secret_rules,
        boundaries=boundary_policies,
        classification=classification_policy,
        disabled_rules=disabled_rules,
        disabled_rules_for_session=disabled_rules_for_session,
        effective_policy_hash=_effective_hash(
            policy_version,
            secret_rules,
            boundary_policies,
            classification_policy,
            disabled_rules,
            disabled_rules_for_session,
        ),
    )


def _resolve_override_file(override_path: str | Path | None | object) -> Path | None:
    if override_path is _USE_DEFAULT_OVERRIDE:
        return paths.config_path("policy.override.yaml")
    if override_path is None:
        return None
    return Path(override_path)


def is_rule_disabled(policy: EffectivePolicy, rule_id: str, session_ref: str | None = None) -> bool:
    if rule_id in policy.disabled_rules:
        return True
    if session_ref is None:
        return False
    return rule_id in policy.disabled_rules_for_session.get(session_ref, frozenset())


def _validate_major_version(policy_version: str) -> None:
    major = policy_version.split(".", 1)[0]
    if major != SUPPORTED_POLICY_MAJOR:
        raise PolicyVersionError(f"unsupported policy major version: {policy_version}")


def _secret_rules(raw_rules: object) -> dict[str, SecretRule]:
    rules: dict[str, SecretRule] = {}
    for rule in raw_rules:
        if not isinstance(rule, Mapping):
            continue
        rule_id = _required_string(rule, "id")
        detector = _required_string(rule, "detector")
        pattern = _required_string(rule, "pattern")
        severity = _required_string(rule, "severity")
        description = _required_string(rule, "description")
        if not rule_id.strip():
            raise PolicyError("rule id cannot be empty")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PolicyError(f"invalid regex for rule {rule_id}: {exc}") from exc
        rules[rule_id] = SecretRule(
            rule_id=rule_id,
            detector=detector,
            pattern=pattern,
            severity=severity,
            description=description,
        )
    return rules


def _required_string(mapping: Mapping[str, Any], key: str) -> str:
    if key not in mapping:
        raise PolicyError(f"missing required policy field {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise PolicyError(f"policy field {key} must be a string")
    return value


def _classification_policy(raw: Mapping[str, Any]) -> ClassificationPolicy:
    return ClassificationPolicy(
        levels=tuple(str(level) for level in raw.get("levels", [])),
        unknown_project_default=str(raw["unknown_project_default"]),
        redaction_hit_default=str(raw["redaction_hit_default"]),
        manual_precedence=bool(raw.get("manual_precedence", False)),
        project_defaults=_project_defaults(raw.get("project_defaults", [])),
    )


def _project_defaults(raw_projects: object) -> dict[str, ProjectDefault]:
    projects: dict[str, ProjectDefault] = {}
    for project in raw_projects:
        if not isinstance(project, Mapping):
            continue
        project_name = str(project["project"])
        projects[project_name] = ProjectDefault(
            project=project_name,
            level=str(project["level"]),
            reason=str(project["reason"]),
            roots=tuple(str(root) for root in project.get("roots", [])),
            remotes=tuple(str(remote) for remote in project.get("remotes", [])),
        )
    return projects


def _merge_project_defaults(
    classification_policy: ClassificationPolicy,
    raw_projects: object,
) -> ClassificationPolicy:
    project_defaults = dict(classification_policy.project_defaults)
    for raw_project in raw_projects:
        if not isinstance(raw_project, Mapping):
            continue
        project_name = str(raw_project["project"])
        existing = project_defaults.get(project_name)
        project = ProjectDefault(
            project=project_name,
            level=str(raw_project["level"]),
            reason=str(raw_project["reason"]),
            roots=tuple(str(root) for root in raw_project.get("roots", existing.roots if existing else [])),
            remotes=tuple(str(remote) for remote in raw_project.get("remotes", existing.remotes if existing else [])),
        )
        project_defaults[project.project] = project
    return replace(classification_policy, project_defaults=project_defaults)


def _boundary_policies(raw: Mapping[str, Any]) -> dict[str, BoundaryPolicy]:
    policies: dict[str, BoundaryPolicy] = {}
    for boundary in raw.get("boundaries", []):
        hooks = {str(hook["name"]): str(hook["fail_mode"]) for hook in boundary.get("hooks", [])}
        policies[str(boundary["id"])] = BoundaryPolicy(
            boundary_id=str(boundary["id"]),
            status=str(boundary["status"]),
            hooks=hooks,
            retry_count=int(boundary["retry_count"]),
            retry_backoff_ms=int(boundary["retry_backoff_ms"]),
            audit_required=bool(boundary["audit_required"]),
        )
    return policies


def _effective_hash(*parts: object) -> str:
    payload = json.dumps(_to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (frozenset, set)):
        return [_to_jsonable(item) for item in sorted(value)]
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value]
    return value
