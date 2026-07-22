"""
Janitor decision logic: decay and reactivation rules.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from paulsha_hippo.janitor.config import JanitorConfig
from paulsha_hippo.janitor.record_source import KnowledgeRecord

SCHEMA_VERSION = "1"
LINT_TITLE_UNTITLED = "title-untitled"
LINT_RAW_REMOTE_KEY = "raw-remote-key"

SourcePathCheck = Callable[[KnowledgeRecord], bool | None]


def _default_source_path_exists(record: KnowledgeRecord) -> bool | None:
    """Default source path existence checker."""
    repo = record.provenance.get("repo")
    path = record.provenance.get("path")
    
    if not repo or not path:
        return None
    
    candidate = Path(repo) / path
    return candidate.exists()


def _parse_ts(value: str) -> datetime | None:
    """Parse ISO timestamp string to datetime."""
    try:
        # Replace Z with +00:00 for Python's fromisoformat
        normalized = value.replace('Z', '+00:00')
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        return None


def _age_days(base: datetime, now: datetime) -> float:
    """Calculate age in days between base and now."""
    delta = now - base
    return delta.total_seconds() / 86400.0


def _ttl_base(record: KnowledgeRecord, lc_info: dict[str, Any]) -> datetime | None:
    """Determine TTL base timestamp for a record."""
    captured_dt = _parse_ts(record.captured_at)
    if captured_dt is None:
        return None
    
    # Anti-flap: if record is active and since_ts is newer, use that
    state = lc_info.get("state", "active")
    if state == "active":
        since_ts = lc_info.get("since_ts")
        if since_ts:
            since_dt = _parse_ts(since_ts)
            if since_dt and since_dt > captured_dt:
                return since_dt
    
    return captured_dt


def _decide_decay(
    record: KnowledgeRecord,
    superseded_by: dict[str, str],
    config: JanitorConfig,
    now: datetime,
    lc_info: dict[str, Any],
    source_path_exists: SourcePathCheck
) -> dict[str, Any] | None:
    """Decide if record should decay."""
    # Priority 1: superseded
    if config.decay_superseded and record.record_id in superseded_by:
        return {
            "reason": "superseded",
            "detail": {"superseded_by": superseded_by[record.record_id]}
        }
    
    # Priority 2: source_invalid
    if config.check_provenance_path:
        path_result = source_path_exists(record)
        if path_result is False:  # Only definite False triggers decay
            return {
                "reason": "source_invalid",
                "detail": {"check": "provenance_path"}
            }
    
    # Priority 3: ttl_expired
    ttl_base = _ttl_base(record, lc_info)
    if ttl_base is None:
        return None
    
    # Determine threshold
    threshold = config.default_decay_age_days
    
    age = _age_days(ttl_base, now)
    if age > threshold:
        return {
            "reason": "ttl_expired",
            "detail": {"age_days": age, "threshold_days": threshold}
        }
    
    return None


def _decide_reactivation(
    record: KnowledgeRecord,
    import_index: dict[str, list[dict[str, str]]],
    lc_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Decide if decayed record should reactivate."""
    state = lc_info.get("state", "active")
    if state != "decayed":
        return None
    
    decay_since = lc_info.get("since_ts")
    decay_dt = _parse_ts(decay_since)
    if decay_dt is None:
        return None
    
    # Unknown source keys don't reactivate
    source_key = record.source_key
    if source_key.endswith(":_unknown") or source_key.startswith("_unknown"):
        return None
    
    # Check for reimport events
    events = import_index.get(source_key, [])
    if not isinstance(events, list):
        return None
    
    # Find latest event after decay
    latest = None
    latest_dt = None
    for event in events:
        if not isinstance(event, dict):
            continue
        recorded_at = event.get("recorded_at", "")
        recorded_dt = _parse_ts(recorded_at)
        if recorded_dt and recorded_dt > decay_dt:
            if latest is None or latest_dt is None or recorded_dt > latest_dt:
                latest = event
                latest_dt = recorded_dt
    
    if latest is None:
        return None
    
    return {
        "reason": "reimport",
        "detail": {
            "import_status": latest.get("status", ""),
            "import_ts": latest["recorded_at"]
        }
    }


def _decayed_event(
    record: KnowledgeRecord,
    decision: dict[str, Any],
    now_str: str,
    config_hash: str
) -> dict[str, Any]:
    """Build decayed event."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "decayed",
        "record_id": record.record_id,
        "ts": now_str,
        "reason": decision["reason"],
        "detail": decision.get("detail", {}),
        "original_ref": {
            "slice_id": record.record_id,
            "source_key": record.source_key,
            "provenance": dict(record.provenance)
        },
        "janitor_config_hash": config_hash
    }


def _reactivation_event(
    record: KnowledgeRecord,
    decision: dict[str, Any],
    now_str: str,
    config_hash: str
) -> dict[str, Any]:
    """Build reactivation event."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "reactivation",
        "record_id": record.record_id,
        "ts": now_str,
        "reason": decision["reason"],
        "agent_ref": record.source_key,
        "detail": decision.get("detail", {}),
        "janitor_config_hash": config_hash
    }


def plan_scan(
    records: list[KnowledgeRecord],
    import_index: dict[str, list[dict[str, str]]],
    lc_state: dict[str, dict[str, str]],
    config: JanitorConfig,
    now: str,
    config_hash: str,
    source_path_exists: SourcePathCheck = _default_source_path_exists
) -> list[dict[str, Any]]:
    """
    Plan decay/reactivation events for records.
    
    Pure function that evaluates all records and returns event list.
    
    Args:
        records: List of knowledge records to evaluate
        import_index: Mapping of source_key -> list of import events
        lc_state: Lifecycle state mapping record_id -> {state, since_ts}
        config: Janitor configuration
        now: Current timestamp string (ISO format)
        config_hash: Hash of janitor config
        source_path_exists: Callable to check if source path exists
    
    Returns:
        List of event dictionaries (decayed or reactivation)
    """
    # Parse now with fallback
    now_dt = _parse_ts(now)
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    
    # Sort records for determinism
    sorted_records = sorted(records, key=lambda r: r.record_id)

    # Build superseded_by index
    superseded_by: dict[str, str] = {}
    for record in sorted_records:
        for old_id in record.supersedes:
            if old_id == record.record_id:
                continue
            if old_id not in superseded_by:
                superseded_by[old_id] = record.record_id
    
    events: list[dict[str, Any]] = []
    
    for record in sorted_records:
        lc_info = lc_state.get(record.record_id, {})
        if not isinstance(lc_info, dict):
            lc_info = {}
        state = lc_info.get("state", "active")
        
        if state == "active":
            # Evaluate decay
            decision = _decide_decay(record, superseded_by, config, now_dt, lc_info, source_path_exists)
            if decision:
                events.append(_decayed_event(record, decision, now, config_hash))
        else:
            # Evaluate reactivation
            decision = _decide_reactivation(record, import_index, lc_info)
            if decision:
                events.append(_reactivation_event(record, decision, now, config_hash))
    
    return events


def plan_lint(records: list[KnowledgeRecord]) -> list[dict[str, Any]]:
    """Return advisory lint findings for hygiene issues in knowledge records."""
    findings: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item.record_id):
        if record.title == "untitled":
            findings.append(
                {
                    "rule": LINT_TITLE_UNTITLED,
                    "record_id": record.record_id,
                    "path": str(record.path),
                    "project": record.project,
                }
            )
        # Remote-form project IDs are canonical metadata.  Filesystem placement
        # uses a separate collision-resistant directory key, so a slash in the
        # project ID is not itself a hygiene defect.  Keep the legacy
        # raw_remote_key summary field for schema compatibility, but do not emit
        # false-positive findings for valid rich IDs.
    return findings
