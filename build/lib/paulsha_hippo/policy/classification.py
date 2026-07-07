from __future__ import annotations

from dataclasses import dataclass

from .models import EffectivePolicy
from .redaction import PolicyHit


@dataclass(frozen=True)
class ClassificationResult:
    level: str
    reason: str
    policy_hash: str
    source: str


def classify_artifact(
    *,
    policy: EffectivePolicy,
    project_slug: str,
    redaction_hits: tuple[PolicyHit, ...],
) -> ClassificationResult:
    default = policy.classification.project_defaults.get(project_slug)
    if default is not None:
        result = ClassificationResult(default.level, default.reason, policy.effective_policy_hash, "default_rule")
    else:
        result = ClassificationResult(
            policy.classification.unknown_project_default,
            "unknown project default",
            policy.effective_policy_hash,
            "default_rule",
        )
    if redaction_hits and _is_more_restrictive(policy, policy.classification.redaction_hit_default, result.level):
        return ClassificationResult(
            level=policy.classification.redaction_hit_default,
            reason="redaction hits present",
            policy_hash=policy.effective_policy_hash,
            source="default_rule",
        )
    return result


def _is_more_restrictive(policy: EffectivePolicy, candidate: str, current: str) -> bool:
    levels = tuple(policy.classification.levels)
    try:
        return levels.index(candidate) > levels.index(current)
    except ValueError:
        return False
