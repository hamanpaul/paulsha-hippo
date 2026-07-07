from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


SUPPORTED_POLICY_MAJOR = "0"


class PolicyError(Exception):
    """Base error for policy loading and evaluation."""


class PolicyVersionError(PolicyError):
    """Raised when the policy major version is unsupported."""


class PolicyExecutionError(PolicyError):
    """Raised when a policy boundary cannot be executed safely."""


@dataclass(frozen=True)
class SecretRule:
    rule_id: str
    detector: str
    pattern: str
    severity: str
    description: str


@dataclass(frozen=True)
class BoundaryPolicy:
    boundary_id: str
    status: str
    hooks: Mapping[str, str]
    retry_count: int
    retry_backoff_ms: int
    audit_required: bool


@dataclass(frozen=True)
class ProjectDefault:
    project: str
    level: str
    reason: str
    roots: tuple[str, ...] = ()
    remotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassificationPolicy:
    levels: tuple[str, ...]
    unknown_project_default: str
    redaction_hit_default: str
    manual_precedence: bool
    project_defaults: Mapping[str, ProjectDefault]


@dataclass(frozen=True)
class EffectivePolicy:
    policy_version: str
    secret_rules: Mapping[str, SecretRule]
    boundaries: Mapping[str, BoundaryPolicy]
    classification: ClassificationPolicy
    disabled_rules: frozenset[str]
    disabled_rules_for_session: Mapping[str, frozenset[str]]
    effective_policy_hash: str
