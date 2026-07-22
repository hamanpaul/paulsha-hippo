"""Release-readiness helpers with an exact, versioned required-gate set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


REQUIRED_GATES = tuple([f"AR-{index:02d}" for index in range(1, 15)] + ["IC-01", "IC-02"])
ALLOWED_STATES = frozenset({"pending", "passed", "failed", "blocked", "skipped"})


def load_matrix(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_matrix(value)
    return value


def validate_matrix(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "1":
        raise ValueError("unsupported readiness matrix schema")
    gates = value.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(REQUIRED_GATES):
        raise ValueError("readiness matrix gate set is incomplete")
    for gate in REQUIRED_GATES:
        row = gates[gate]
        if not isinstance(row, Mapping) or row.get("state") not in ALLOWED_STATES:
            raise ValueError(f"invalid readiness state: {gate}")
        if "evidence" not in row or "rerun" not in row or "timestamp" not in row:
            raise ValueError(f"readiness gate fields are incomplete: {gate}")
        if row.get("state") == "passed" and not row.get("evidence"):
            raise ValueError(f"passed readiness gate lacks evidence: {gate}")


def bind_candidate(value: Mapping[str, Any], *, commit: str, wheel_sha256: str) -> dict[str, Any]:
    """Bind a matrix to a candidate and invalidate prior gate evidence on drift."""
    if not commit or not wheel_sha256:
        raise ValueError("candidate identity must be non-empty")
    result = json.loads(json.dumps(value))
    old_commit = result.get("candidate_commit")
    old_wheel = result.get("wheel_sha256")
    result["candidate_commit"] = commit
    result["wheel_sha256"] = wheel_sha256
    if old_commit not in (None, commit) or old_wheel not in (None, wheel_sha256):
        for row in result["gates"].values():
            if row.get("state") == "passed":
                row["state"] = "pending"
                row["evidence"] = None
                row["rerun"] = "candidate identity changed"
    validate_matrix(result)
    return result


__all__ = ["ALLOWED_STATES", "REQUIRED_GATES", "bind_candidate", "load_matrix", "validate_matrix"]
