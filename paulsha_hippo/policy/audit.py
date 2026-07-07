from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from .models import EffectivePolicy
from .redaction import PolicyHit


@dataclass(frozen=True)
class PolicyAuditEvent:
    boundary: str
    component: str
    session_ref: str
    policy_version: str
    effective_policy_hash: str
    rule_id: str
    detector: str
    line_no: int
    action: str


def append_policy_audit(path: str | Path, event: PolicyAuditEvent) -> None:
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat()}
    record.update(asdict(event))
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def build_policy_audit_events(
    *,
    boundary: str,
    component: str,
    session_ref: str,
    policy: EffectivePolicy,
    hits: Iterable[PolicyHit],
) -> tuple[PolicyAuditEvent, ...]:
    return tuple(
        PolicyAuditEvent(
            boundary=boundary,
            component=component,
            session_ref=session_ref,
            policy_version=policy.policy_version,
            effective_policy_hash=policy.effective_policy_hash,
            rule_id=hit.rule_id,
            detector=hit.detector,
            line_no=hit.line_no,
            action=hit.action,
        )
        for hit in hits
    )


def append_policy_audits(path: str | Path, events: Iterable[PolicyAuditEvent]) -> None:
    for event in events:
        append_policy_audit(path, event)
