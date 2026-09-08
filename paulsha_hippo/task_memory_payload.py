"""Task memory payload helpers for issue #146."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ledger.processing import redact_secret_text

ALLOWED_DELIVERY_MODES = frozenset(
    {"inline", "snapshot", "note_fetch", "ineligible", "failure"}
)
AUTHORIZED_CANDIDATE_STATUS = "authorized"
MAX_CANDIDATES = 3
MAX_INTENT_CHARS = 280
MAX_SUMMARY_CHARS = 240
MAX_EXCERPT_CHARS = 800


def build_task_memory_payload(
    *,
    schema_version: Any,
    task_id: Any,
    intent: Any,
    candidates: Sequence[Mapping[str, Any]] | None,
    delivery: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] | None,
    producer: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a normalized, shareable-safe payload envelope."""

    payload = {
        "schema_version": _require_text(schema_version, field="schema_version"),
        "task_id": _require_text(task_id, field="task_id"),
        "intent": _bounded_text(intent, field="intent", limit=MAX_INTENT_CHARS),
        "candidates": _build_candidates(candidates or ()),
        "delivery": _normalize_delivery(delivery),
        "evidence": _normalize_evidence(evidence or ()),
        "producer": _normalize_identity(producer, field="producer"),
        "adapter": _normalize_identity(adapter, field="adapter"),
    }
    return validate_task_memory_payload(payload)


def validate_task_memory_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a payload while preserving unknown optional fields."""

    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")

    normalized = deepcopy(dict(payload))
    normalized["schema_version"] = _require_text(
        payload.get("schema_version"), field="schema_version"
    )
    normalized["task_id"] = _require_text(payload.get("task_id"), field="task_id")
    normalized["intent"] = _bounded_text(
        payload.get("intent"), field="intent", limit=MAX_INTENT_CHARS
    )
    normalized["candidates"] = _validate_candidates(payload.get("candidates"))
    normalized["delivery"] = _normalize_delivery(payload.get("delivery"))
    normalized["evidence"] = _normalize_evidence(payload.get("evidence"))
    normalized["producer"] = _normalize_identity(payload.get("producer"), field="producer")
    normalized["adapter"] = _normalize_identity(payload.get("adapter"), field="adapter")
    return normalized


def summarize_delivery_outcome(
    *,
    mode: Any,
    events: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Summarize delivery/read semantics without inflating read KPI."""

    normalized_mode = _normalize_mode(mode)
    normalized_events = _normalize_events(events or ())
    content_returned = normalized_mode == "note_fetch" and any(
        event["kind"] == "returned" for event in normalized_events
    )
    applied = content_returned and any(
        event["kind"] == "applied" for event in normalized_events
    )
    failure_reason = None
    if not content_returned:
        for event in normalized_events:
            if event["kind"] == "failed" and event.get("reason"):
                failure_reason = str(event["reason"])
                break
    return {
        "mode": normalized_mode,
        "content_returned": content_returned,
        "counts_as_read": content_returned,
        "counts_as_applied": applied,
        "failure_reason": failure_reason,
    }


def _build_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for candidate in candidates:
        item = _normalize_candidate(candidate)
        if item["authorization"]["status"] != AUTHORIZED_CANDIDATE_STATUS:
            continue
        normalized.append(((item["rank"], item["ref"]), item))
    normalized.sort(key=lambda item: item[0])
    return [item for _key, item in normalized[:MAX_CANDIDATES]]


def _validate_candidates(candidates: Any) -> list[dict[str, Any]]:
    rows = _normalize_candidate_sequence(candidates)
    if len(rows) > MAX_CANDIDATES:
        raise ValueError(f"candidates must contain at most {MAX_CANDIDATES} items")
    for candidate in rows:
        if candidate["authorization"]["status"] != AUTHORIZED_CANDIDATE_STATUS:
            raise ValueError("candidate authorization.status must be authorized")
    return rows


def _normalize_candidate_sequence(candidates: Any) -> list[dict[str, Any]]:
    if candidates is None:
        return []
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise ValueError("candidates must be a sequence")
    normalized: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for candidate in candidates:
        item = _normalize_candidate(candidate)
        normalized.append(((item["rank"], item["ref"]), item))
    normalized.sort(key=lambda item: item[0])
    return [item for _key, item in normalized]


def _normalize_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be a mapping")
    normalized = deepcopy(dict(candidate))
    normalized["ref"] = _require_text(candidate.get("ref"), field="candidate.ref")
    normalized["rank"] = _normalize_rank(candidate.get("rank"))
    normalized["summary"] = _sanitize_public_text(
        candidate.get("summary"), field="candidate.summary", limit=MAX_SUMMARY_CHARS
    )
    normalized["authorization"] = _normalize_status_block(
        candidate.get("authorization"),
        field="candidate.authorization",
        allowed={"authorized", "denied", "unknown"},
    )
    normalized["availability"] = _normalize_status_block(
        candidate.get("availability"),
        field="candidate.availability",
        allowed={"available", "unavailable", "unknown"},
    )
    if "excerpt" in candidate:
        normalized["excerpt"] = _sanitize_public_text(
            candidate.get("excerpt"), field="candidate.excerpt", limit=MAX_EXCERPT_CHARS
        )
    return normalized


def _normalize_delivery(delivery: Any) -> dict[str, Any]:
    if not isinstance(delivery, Mapping):
        raise ValueError("delivery must be a mapping")
    normalized = deepcopy(dict(delivery))
    normalized["mode"] = _normalize_mode(delivery.get("mode"))

    capabilities = delivery.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ValueError("delivery.capabilities must be a mapping")
    normalized_caps = deepcopy(dict(capabilities))
    for key in ("inline", "snapshot", "note_fetch"):
        normalized_caps[key] = bool(capabilities.get(key, False))
    normalized["capabilities"] = normalized_caps

    required_capability = {
        "inline": "inline",
        "snapshot": "snapshot",
        "note_fetch": "note_fetch",
    }.get(normalized["mode"])
    if required_capability and not normalized_caps.get(required_capability, False):
        raise ValueError(
            f"delivery.capabilities.{required_capability} must be true for mode {normalized['mode']}"
        )
    return normalized


def _normalize_evidence(evidence: Any) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
        raise ValueError("evidence must be a sequence")
    normalized: list[dict[str, Any]] = []
    for event in evidence:
        if not isinstance(event, Mapping):
            raise ValueError("evidence events must be mappings")
        item = deepcopy(dict(event))
        if "kind" in event:
            item["kind"] = _require_text(event.get("kind"), field="evidence.kind")
        if "reason" in event and event.get("reason") is not None:
            item["reason"] = str(event["reason"])
        normalized.append(item)
    return normalized


def _normalize_identity(identity: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(identity, Mapping):
        raise ValueError(f"{field} must be a mapping")
    normalized = deepcopy(dict(identity))
    normalized["id"] = _require_text(identity.get("id"), field=f"{field}.id")
    if "version" in identity and identity.get("version") is not None:
        normalized["version"] = _bounded_text(
            identity["version"], field=f"{field}.version", limit=MAX_SUMMARY_CHARS
        )
    return normalized


def _normalize_status_block(
    block: Any,
    *,
    field: str,
    allowed: set[str],
) -> dict[str, Any]:
    if not isinstance(block, Mapping):
        raise ValueError(f"{field} must be a mapping")
    normalized = deepcopy(dict(block))
    status = _require_text(block.get("status"), field=f"{field}.status")
    if status not in allowed:
        raise ValueError(f"{field}.status must be one of {sorted(allowed)}")
    normalized["status"] = status
    if "reason" in block and block.get("reason") is not None:
        normalized["reason"] = _bounded_text(
            block["reason"], field=f"{field}.reason", limit=MAX_SUMMARY_CHARS
        )
    return normalized


def _normalize_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("events must contain mappings")
        item = deepcopy(dict(event))
        item["kind"] = _require_text(event.get("kind"), field="events.kind")
        if "reason" in event and event.get("reason") is not None:
            item["reason"] = str(event["reason"])
        normalized.append(item)
    return normalized


def _normalize_mode(mode: Any) -> str:
    value = _require_text(mode, field="delivery.mode")
    if value not in ALLOWED_DELIVERY_MODES:
        raise ValueError(f"delivery.mode must be one of {sorted(ALLOWED_DELIVERY_MODES)}")
    return value


def _normalize_rank(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("candidate.rank must be an integer")
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate.rank must be an integer") from exc
    if rank < 0:
        raise ValueError("candidate.rank must be >= 0")
    return rank


def _sanitize_public_text(value: Any, *, field: str, limit: int) -> str:
    text = _bounded_text(value, field=field, limit=limit)
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return redact_secret_text(text)


def _bounded_text(value: Any, *, field: str, limit: int) -> str:
    text = _require_text(value, field=field)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _require_text(value: Any, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


__all__ = [
    "ALLOWED_DELIVERY_MODES",
    "build_task_memory_payload",
    "summarize_delivery_outcome",
    "validate_task_memory_payload",
]
