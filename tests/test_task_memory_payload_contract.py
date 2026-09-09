from __future__ import annotations

import importlib
import json

import pytest


def _contract():
    try:
        contract = importlib.import_module("paulsha_hippo.task_memory_payload")
    except ModuleNotFoundError:
        pytest.fail(
            "issue #146 contract missing: expected paulsha_hippo.task_memory_payload "
            "with build_task_memory_payload(), validate_task_memory_payload(), "
            "and summarize_delivery_outcome()"
        )
    missing = [
        name
        for name in (
            "build_task_memory_payload",
            "validate_task_memory_payload",
            "summarize_delivery_outcome",
        )
        if not callable(getattr(contract, name, None))
    ]
    assert missing == [], f"issue #146 contract missing callables: {', '.join(missing)}"
    return contract


def test_task_memory_payload_bounds_candidates_redacts_and_keeps_optional_fields():
    contract = _contract()
    candidates = [
        {
            "ref": "cand-2",
            "rank": 2,
            "summary": "Authorized follow-up note",
            "authorization": {"status": "authorized"},
            "availability": {"status": "available"},
            "excerpt": "second candidate body",
        },
        {
            "ref": "cand-1",
            "rank": 1,
            "summary": "Authorized first note",
            "authorization": {"status": "authorized"},
            "availability": {"status": "available"},
            "excerpt": "OPENAI_API_KEY=sk-prod-secret-token",
        },
        {
            "ref": "cand-4",
            "rank": 4,
            "summary": "Provider offline note",
            "authorization": {"status": "authorized"},
            "availability": {
                "status": "unavailable",
                "reason": "provider_unavailable",
            },
            "excerpt": "body should not be required to expose availability",
        },
        {
            "ref": "cand-3",
            "rank": 3,
            "summary": "Denied note",
            "authorization": {
                "status": "denied",
                "reason": "permission_denied",
            },
            "availability": {"status": "available"},
            "excerpt": "private denied body",
        },
        {
            "ref": "cand-5",
            "rank": 5,
            "summary": "Authorized overflow note",
            "authorization": {"status": "authorized"},
            "availability": {"status": "available"},
            "excerpt": "fifth candidate body",
        },
    ]
    authorized_input_refs = sorted(
        candidate["ref"]
        for candidate in candidates
        if candidate["authorization"]["status"] == "authorized"
    )
    assert authorized_input_refs == ["cand-1", "cand-2", "cand-4", "cand-5"]

    payload = contract.build_task_memory_payload(
        schema_version="1",
        task_id="task-146-red",
        intent="summarize task memory needed for the current job",
        candidates=candidates,
        delivery={
            "mode": "note_fetch",
            "capabilities": {
                "inline": False,
                "snapshot": False,
                "note_fetch": True,
            },
        },
        evidence=[],
        producer={"id": "hippo-core", "version": "0.1.2"},
        adapter={"id": "host-adapter"},
    )

    assert payload["schema_version"] == "1"
    assert payload["task_id"] == "task-146-red"
    assert payload["intent"] == "summarize task memory needed for the current job"
    selected_refs = [candidate["ref"] for candidate in payload["candidates"]]
    assert selected_refs == ["cand-1", "cand-2", "cand-4"]
    assert len(payload["candidates"]) == 3
    assert all(candidate["ref"] != "cand-3" for candidate in payload["candidates"])
    assert "cand-5" not in selected_refs
    assert payload["candidates"][0]["authorization"]["status"] == "authorized"
    assert payload["candidates"][-1]["availability"] == {
        "status": "unavailable",
        "reason": "provider_unavailable",
    }

    rendered = json.dumps(payload, ensure_ascii=False)
    assert "sk-prod-secret-token" not in rendered
    assert "private denied body" not in rendered

    payload["delivery"]["host_optional"] = {"materialized_path": "/tmp/task-memory.json"}
    normalized = contract.validate_task_memory_payload(payload)
    assert normalized["delivery"]["mode"] == "note_fetch"
    assert normalized["delivery"]["host_optional"] == {
        "materialized_path": "/tmp/task-memory.json"
    }

    empty = contract.build_task_memory_payload(
        schema_version="1",
        task_id="task-146-empty",
        intent="summarize task memory needed for the current job",
        candidates=[],
        delivery={
            "mode": "ineligible",
            "capabilities": {
                "inline": False,
                "snapshot": False,
                "note_fetch": False,
            },
        },
        evidence=[{"kind": "ineligible", "reason": "provider_unavailable"}],
        producer={"id": "hippo-core", "version": "0.1.2"},
        adapter={"id": "host-adapter"},
    )
    assert empty["candidates"] == []


def test_task_memory_payload_redacts_secret_before_excerpt_truncation():
    contract = _contract()
    token = "ghp_" + "A1b2C3d4" * 5

    payload = contract.build_task_memory_payload(
        schema_version="1",
        task_id="task-146-boundary",
        intent="summarize task memory needed for the current job",
        candidates=[
            {
                "ref": "cand-1",
                "rank": 1,
                "summary": "Boundary secret candidate",
                "authorization": {"status": "authorized"},
                "availability": {"status": "available"},
                "excerpt": ("x" * 790) + " " + token + " trailing",
            }
        ],
        delivery={
            "mode": "note_fetch",
            "capabilities": {
                "inline": False,
                "snapshot": False,
                "note_fetch": True,
            },
        },
        evidence=[],
        producer={"id": "hippo-core", "version": "0.1.2"},
        adapter={"id": "host-adapter"},
    )

    excerpt = payload["candidates"][0]["excerpt"]
    assert len(excerpt) <= 800
    assert "ghp_" not in excerpt
    assert "A1b2C3d4" not in excerpt


def test_delivery_outcome_requires_returned_event_and_keeps_inline_snapshot_out_of_read():
    contract = _contract()

    inline = contract.summarize_delivery_outcome(
        mode="inline",
        events=[{"kind": "context_delivered"}],
    )
    snapshot = contract.summarize_delivery_outcome(
        mode="snapshot",
        events=[{"kind": "materialized"}, {"kind": "offered"}],
    )
    attempted_only = contract.summarize_delivery_outcome(
        mode="note_fetch",
        events=[{"kind": "read_attempt", "candidate_ref": "cand-1"}],
    )
    applied_without_return = contract.summarize_delivery_outcome(
        mode="note_fetch",
        events=[{"kind": "applied", "candidate_ref": "cand-1"}],
    )
    returned = contract.summarize_delivery_outcome(
        mode="note_fetch",
        events=[
            {"kind": "read_attempt", "candidate_ref": "cand-1"},
            {"kind": "returned", "candidate_ref": "cand-1"},
            {"kind": "applied", "candidate_ref": "cand-1"},
        ],
    )
    failed = contract.summarize_delivery_outcome(
        mode="failure",
        events=[
            {"kind": "read_attempt", "candidate_ref": "cand-1"},
            {
                "kind": "failed",
                "candidate_ref": "cand-1",
                "reason": "permission_denied",
            },
        ],
    )

    assert inline["content_returned"] is False
    assert inline["counts_as_read"] is False
    assert inline["counts_as_applied"] is False

    assert snapshot["content_returned"] is False
    assert snapshot["counts_as_read"] is False
    assert snapshot["counts_as_applied"] is False

    assert attempted_only["content_returned"] is False
    assert attempted_only["counts_as_read"] is False
    assert attempted_only["counts_as_applied"] is False

    assert applied_without_return["content_returned"] is False
    assert applied_without_return["counts_as_read"] is False
    assert applied_without_return["counts_as_applied"] is False

    assert returned["content_returned"] is True
    assert returned["counts_as_read"] is True
    assert returned["counts_as_applied"] is True

    assert failed["content_returned"] is False
    assert failed["counts_as_read"] is False
    assert failed["counts_as_applied"] is False
    assert failed["failure_reason"] == "permission_denied"
