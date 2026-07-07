from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .schema import PHASES


EVENT_STATUS_MAP = {
    "phase.requested": "requested",
    "phase.artifact_submitted": "submitted",
    "phase.gate_passed": "passed",
    "phase.gate_failed": "failed",
}


@dataclass(frozen=True)
class PhaseState:
    current_phase: str | None
    phase_status: dict[str, str]
    blocked_phase: str | None


def append_event(
    *,
    path: Path | str,
    kind: str,
    slice_id: str,
    phase: str,
    project: str,
    actor: str,
    artifact_ref: str | None = None,
    ts: str | None = None,
    meta: dict[str, object] | None = None,
) -> dict[str, object]:
    if kind not in EVENT_STATUS_MAP:
        raise ValueError(f"unsupported lifecycle event kind: {kind}")
    if phase not in PHASES:
        raise ValueError(f"unsupported lifecycle phase: {phase}")
    event = {
        "kind": kind,
        "slice_id": slice_id,
        "phase": phase,
        "project": project,
        "actor": actor,
        "artifact_ref": artifact_ref,
        "ts": ts or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "meta": meta or {},
    }

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True))
        handle.write("\n")

    return event


def read_events(
    path: Path | str,
    *,
    slice_id: str | None = None,
    project: str | None = None,
) -> list[dict[str, object]]:
    source = Path(path)
    if not source.exists():
        return []

    records: list[dict[str, object]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if slice_id and event.get("slice_id") != slice_id:
                continue
            if project and event.get("project") != project:
                continue
            records.append(event)
    return records


def rebuild_phase_state(
    path: Path | str,
    *,
    slice_id: str | None = None,
    project: str | None = None,
) -> PhaseState:
    phase_status: dict[str, str] = {}
    current_phase: str | None = None
    blocked_phase: str | None = None

    for event in read_events(path, slice_id=slice_id, project=project):
        kind = str(event["kind"])
        phase = str(event["phase"])
        status = EVENT_STATUS_MAP.get(kind)
        if status is None:
            continue
        phase_status[phase] = status

        if kind == "phase.gate_failed":
            blocked_phase = phase
            current_phase = f"blocked:{phase}"
            continue

        if kind == "phase.gate_passed" and blocked_phase == phase:
            blocked_phase = None

        if blocked_phase is None:
            current_phase = phase

    return PhaseState(
        current_phase=current_phase,
        phase_status=phase_status,
        blocked_phase=blocked_phase,
    )
