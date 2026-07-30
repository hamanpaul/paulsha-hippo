from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo.agent_profiles import AgentRunResult
from paulsha_hippo.atomizer import provenance as provenance_module
from paulsha_hippo.atomizer import slice_frontmatter
from paulsha_hippo.atomizer.provenance import provenance_from_result, safe_provenance
from paulsha_hippo.moc import frontmatter_io


def test_provenance_keeps_model_truth_and_redacts_failure_evidence():
    result = AgentRunResult(
        "co-gem", "7", 3, 2, "local-model", "low", None, "unverified", "cmd-hash",
        1.25, "process", f"Bearer super-secret {Path.home() / 'bin' / 'agent'}\x1b[31m", 17,
        "degraded-success",
    )
    provenance = provenance_from_result(
        result,
        config_hash="config-hash",
        skill_hash="skill-hash",
        build="commit-hash",
        attempts=[result] * 8,
    )

    assert provenance["requested_model"] == "local-model"
    assert provenance["observed_model"] is None
    assert provenance["model_verification"] == "unverified"
    assert "super-secret" not in json.dumps(provenance)
    assert "paul_chen" not in json.dumps(provenance)
    assert "\\x1b" not in json.dumps(provenance)
    assert "REDACTED" in provenance["stderr"]
    assert len(provenance["attempts"]) == 6
    assert all("super-secret" not in json.dumps(attempt) for attempt in provenance["attempts"])


def test_distiller_provenance_round_trips_through_atom_frontmatter():
    value = {
        "profile_id": "claude",
        "profile_revision": "2",
        "tier": 1,
        "attempt_index": 1,
        "requested_model": "claude-sonnet",
        "requested_effort": "high",
        "observed_model": None,
        "model_verification": "unverified",
        "command_fingerprint": "abc",
        "fallback_reason": None,
        "config_hash": "config",
        "skill_hash": "skill",
        "hippo_version": "0.1.1",
        "build_commit": "commit",
        "response_schema": "1",
        "stderr": "",
        "exit_code": 0,
        "attempts": [
            {
                "profile_id": "claude",
                "profile_revision": "2",
                "tier": 1,
                "priority": 1,
                "attempt_index": 1,
                "requested_model": "claude-sonnet",
                "requested_effort": "high",
                "observed_model": None,
                "model_verification": "unverified",
                "command_fingerprint": "abc",
                "response_schema": "1",
                "elapsed_seconds": 0.2,
                "failure_category": "auth",
                "fallback_reason": None,
                "stderr": "login required",
                "exit_code": 1,
            }
        ],
    }
    atom = slice_frontmatter.Slice(
        slice_id="sl-provenance",
        frontmatter={
            "phase": "review", "project": "demo", "slice_id": "sl-provenance",
            "artifact_kind": "report", "version": "1", "created_at": "2026-07-22T00:00:00Z",
            "created_by": "claude", "source_session": "s1", "gate_required": False,
            "checksum": "", "memory_layer": "knowledge", "source_agent": "claude",
            "captured_at": "2026-07-22T00:00:00Z", "provenance": {}, "supersedes": [],
            "distiller": value, "title": "具體標題", "atom_title": "具體標題",
        },
        body="body\n",
    )
    atom = slice_frontmatter.Slice(
        slice_id=atom.slice_id,
        frontmatter={**atom.frontmatter, "checksum": ""},
        body=atom.body,
    )
    atom = slice_frontmatter.Slice(
        slice_id=atom.slice_id,
        frontmatter={
            **atom.frontmatter,
            "checksum": __import__("hashlib").sha256(atom.body.encode()).hexdigest(),
        },
        body=atom.body,
    )

    parsed, _body = frontmatter_io.read(slice_frontmatter.render(atom))

    assert parsed["distiller"]["profile_id"] == "claude"
    assert parsed["distiller"]["tier"] == 1
    assert parsed["distiller"]["model_verification"] == "unverified"
    assert parsed["distiller"]["response_schema"] == "1"
    assert parsed["distiller"]["attempts"][0]["failure_category"] == "auth"
    assert slice_frontmatter.validate(parsed, atom.body) == []


def test_chunk_provenance_round_trips_through_atom_frontmatter():
    """Issue #86 finding: a mixed-profile session's per-chunk provenance must
    actually reach the persisted slice frontmatter, not just the in-memory
    distiller dict. Otherwise the persisted ``distiller.profile_id`` reads as
    if a single profile produced the whole slice, contradicting the
    chunk_provenance recorded for it -- the "distiller.profile_id 說謊"
    scenario the design doc explicitly forbids."""
    value = safe_provenance(
        {
            "profile_id": "second",
            "profile_revision": "1",
            "tier": 2,
            "fallback_reason": "degraded-success",
            "response_schema": "1",
            "stderr": "",
            "exit_code": 0,
            "chunk_provenance": [
                {"chunk_index": 0, "profile_id": "first", "tier": 1, "elapsed_seconds": 1.5},
                {"chunk_index": 1, "profile_id": "second", "tier": 2, "elapsed_seconds": 2.5},
            ],
        }
    )
    atom = slice_frontmatter.Slice(
        slice_id="sl-chunk-provenance",
        frontmatter={
            "phase": "review", "project": "demo", "slice_id": "sl-chunk-provenance",
            "artifact_kind": "report", "version": "1", "created_at": "2026-07-30T00:00:00Z",
            "created_by": "claude", "source_session": "s1", "gate_required": False,
            "checksum": "", "memory_layer": "knowledge", "source_agent": "claude",
            "captured_at": "2026-07-30T00:00:00Z", "provenance": {}, "supersedes": [],
            "distiller": value, "title": "混合 profile", "atom_title": "混合 profile",
        },
        body="body\n",
    )
    atom = slice_frontmatter.Slice(
        slice_id=atom.slice_id,
        frontmatter={
            **atom.frontmatter,
            "checksum": __import__("hashlib").sha256(atom.body.encode()).hexdigest(),
        },
        body=atom.body,
    )

    rendered = slice_frontmatter.render(atom)
    assert "chunk_provenance" in rendered

    parsed, _body = frontmatter_io.read(rendered)

    # The whole point: the slice actually persisted on disk must not lose the
    # per-chunk producer record -- distiller.profile_id staying "second" is
    # only honest because chunk_provenance is also on disk to show chunk 0
    # was produced by "first".
    assert parsed["distiller"]["profile_id"] == "second"
    chunk_provenance = parsed["distiller"]["chunk_provenance"]
    assert [entry["profile_id"] for entry in chunk_provenance] == ["first", "second"]
    assert [entry["chunk_index"] for entry in chunk_provenance] == [0, 1]
    assert slice_frontmatter.validate(parsed, atom.body) == []


def test_safe_provenance_does_not_accept_unbounded_stderr():
    safe = safe_provenance({"profile_id": "x", "stderr": "token=secret\n" + "x" * 1000})
    assert len(safe["stderr"]) <= 500
    assert "secret" not in safe["stderr"]


def test_provenance_defaults_to_runtime_build_identity(monkeypatch):
    monkeypatch.setattr(
        provenance_module,
        "build_identity",
        lambda: {"build_commit": "installed-candidate-commit"},
    )

    value = provenance_from_result(None)

    assert value["build_commit"] == "installed-candidate-commit"


def test_explicit_provenance_build_overrides_runtime_identity(monkeypatch):
    monkeypatch.setattr(
        provenance_module,
        "build_identity",
        lambda: {"build_commit": "installed-candidate-commit"},
    )

    value = provenance_from_result(None, build="explicit-commit")

    assert value["build_commit"] == "explicit-commit"
