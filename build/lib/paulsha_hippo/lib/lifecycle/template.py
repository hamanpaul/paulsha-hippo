from __future__ import annotations

from .schema import PHASES


def build_lifecycle_template(
    *,
    project: str,
    current_slice: str,
    workflow_version: str = "0.1.0",
) -> dict[str, object]:
    return {
        "project": project,
        "current_slice": current_slice,
        "current_phase": "research",
        "workflow_version": workflow_version,
        "last_ship": None,
        "open_rework": [],
        "open_rewind": [],
        "stale_spikes": [],
        "gates": {
            phase: {
                "last_check": None,
                "status": None,
            }
            for phase in PHASES
        },
    }


def render_lifecycle_yaml(
    *,
    project: str,
    current_slice: str,
    workflow_version: str = "0.1.0",
) -> str:
    payload = build_lifecycle_template(
        project=project,
        current_slice=current_slice,
        workflow_version=workflow_version,
    )
    lines = [
        f"project: {payload['project']}",
        f"current_slice: {payload['current_slice']}",
        f"current_phase: {payload['current_phase']}",
        f"workflow_version: {payload['workflow_version']}",
        "last_ship: null",
        "open_rework: []",
        "open_rewind: []",
        "stale_spikes: []",
        "gates:",
    ]
    gates = payload["gates"]
    for phase in PHASES:
        lines.extend(
            [
                f"  {phase}:",
                "    last_check: null",
                "    status: null",
            ]
        )
    return "\n".join(lines) + "\n"
