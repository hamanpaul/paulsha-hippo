from __future__ import annotations

import json
from pathlib import Path

import pytest

from paulsha_hippo import readiness


def test_release_matrix_records_complete_candidate_bound_evidence():
    path = Path(__file__).resolve().parents[1] / "reports" / "verify" / "release-readiness-matrix.json"
    matrix = readiness.load_matrix(path)
    assert all(row["state"] == "passed" for row in matrix["gates"].values())
    assert matrix["candidate_commit"] == "eb2ccb86d7d4c4a91a8f8e2c0a743a677e52b2b1"
    assert matrix["wheel_sha256"] == "b895ef91ab0e7ebb1836779b9c5664e2f32ed6e847e0e4af89fa3de82e5dcc6a"


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
