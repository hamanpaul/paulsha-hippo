from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from paulsha_hippo import readiness


def test_release_matrix_is_schema_valid_and_traceable():
    """The on-disk matrix must stay schema-valid and honestly candidate-bound.

    This intentionally does not hard-code a specific candidate commit, wheel
    hash, or "all gates passed" expectation: those are release-specific facts
    that change every time the matrix is rebound to a new candidate (see
    docs/release-readiness.md). What must always hold, for any candidate, is:
    the file parses under the schema, the candidate commit looks like a real
    git SHA, every gate still carries a rerun instruction, and any gate
    claiming "passed" carries real evidence (load_matrix already enforces the
    latter; asserted again here for clarity).
    """
    path = Path(__file__).resolve().parents[1] / "reports" / "verify" / "release-readiness-matrix.json"
    matrix = readiness.load_matrix(path)
    assert re.fullmatch(r"[0-9a-f]{40}", matrix["candidate_commit"] or "")
    for gate, row in matrix["gates"].items():
        assert row["rerun"], f"gate {gate} must record a rerun command"
        if row["state"] == "passed":
            assert row["evidence"], f"passed gate {gate} must carry evidence"


def test_candidate_drift_invalidates_passed_evidence():
    value = {"schema_version": "1", "candidate_commit": "a", "wheel_sha256": "b", "gates": {gate: {"state": "pending", "evidence": None, "rerun": "x", "timestamp": None} for gate in readiness.REQUIRED_GATES}}
    value["gates"]["AR-01"] = {"state": "passed", "evidence": "old", "rerun": "x", "timestamp": "2026-07-22T00:00:00Z"}
    rebound = readiness.bind_candidate(value, commit="new", wheel_sha256="new-wheel")
    assert rebound["gates"]["AR-01"]["state"] == "pending"
    assert rebound["gates"]["AR-01"]["evidence"] is None


def test_passed_gate_requires_evidence():
    value = {"schema_version": "1", "candidate_commit": None, "wheel_sha256": None, "gates": {gate: {"state": "pending", "evidence": None, "rerun": "x", "timestamp": None} for gate in readiness.REQUIRED_GATES}}
    value["gates"]["AR-01"]["state"] = "passed"
    with pytest.raises(ValueError, match="lacks evidence"):
        readiness.validate_matrix(value)
