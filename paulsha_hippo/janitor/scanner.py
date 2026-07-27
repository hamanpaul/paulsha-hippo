"""
Janitor scanner orchestrator.

Coordinates scanning knowledge records for decay/reactivation and persists
lifecycle events.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from paulsha_hippo.janitor import record_source, rules
from paulsha_hippo.janitor.config import JanitorConfig
from paulsha_hippo.ledger import import_log, lifecycle
from paulsha_hippo.ledger import usage as usage_ledger

# Import statuses that carry new evidence and may reactivate a decayed record.
_REACTIVATION_STATUSES = {"written", "updated"}


def _lifecycle_path(memory_root: Path) -> Path:
    """Get path to lifecycle ledger."""
    return memory_root / "runtime" / "ledger" / "lifecycle.jsonl"


def _build_lc_state(memory_root: Path) -> dict[str, dict[str, str]]:
    """
    Build lifecycle state mapping for rules.
    
    Converts fold_lifecycle output to rules-compatible format:
    - last_state -> state
    - last_event_ts -> since_ts
    """
    lc_path = _lifecycle_path(memory_root)
    events = lifecycle.read_events(lc_path)
    folded = lifecycle.fold_lifecycle(events)
    
    result: dict[str, dict[str, str]] = {}
    for record_id, rec in folded.items():
        state = rec.get("last_state", "active")
        if state is None:
            state = "active"
        since_ts = rec.get("last_event_ts", "")
        result[record_id] = {
            "state": state,
            "since_ts": since_ts if since_ts else ""
        }
    
    return result


def _build_import_index(memory_root: Path) -> tuple[dict[str, list[dict[str, str]]], int]:
    """
    Build import index mapping source_key -> import events.
    
    Reads import.jsonl and groups records by idempotency_key (which serves
    as source_key for matching with records).
    
    Returns:
        Tuple of:
        - Dict mapping source_key to list of dicts with 'status' and 'recorded_at'.
          Only ``written``/``updated`` imports are indexed: those are the events
          that carry new evidence and may legitimately reactivate a decayed
          record. ``hash-duplicate``/``stale-skip`` add nothing and are ignored.
        - Count of skipped malformed/blank lines.
    """
    import_path = memory_root / "runtime" / "ledger" / "import.jsonl"
    records, bad_line_count = import_log.read_import_records_tolerant(import_path)

    index: dict[str, list[dict[str, str]]] = {}
    for rec in records:
        # Extract source_key from idempotency_key
        source_key = rec.get("idempotency_key")
        if not source_key:
            continue

        status = rec.get("status", "")
        if status not in _REACTIVATION_STATUSES:
            continue

        recorded_at = rec.get("recorded_at", "")

        if source_key not in index:
            index[source_key] = []

        index[source_key].append({
            "status": status,
            "recorded_at": recorded_at
        })

    return index, bad_line_count


def _persist_event(memory_root: Path, event: dict[str, Any]) -> None:
    """
    Persist a rules event to lifecycle ledger.
    
    Converts rules event format to lifecycle.append_event arguments.
    """
    lc_path = _lifecycle_path(memory_root)
    
    # Extract metadata fields
    metadata = {
        "schema_version": event.get("schema_version", "1"),
        "detail": event.get("detail", {}),
        "janitor_config_hash": event.get("janitor_config_hash", ""),
    }
    
    # Add event-type-specific fields to metadata
    if "original_ref" in event:
        metadata["original_ref"] = event["original_ref"]
    if "agent_ref" in event:
        metadata["agent_ref"] = event["agent_ref"]
    
    lifecycle.append_event(
        path=lc_path,
        record_id=event["record_id"],
        event_type=event["event_type"],
        source="janitor",
        reason=event["reason"],
        actor="janitor",
        run_id=None,
        metadata=metadata,
        ts=event.get("ts"),
    )


def run_scan(
    memory_root: Path,
    knowledge_root: Path,
    config: JanitorConfig,
    config_hash: str,
    now: str,
    dry_run: bool = False,
    source_path_exists: Callable[[record_source.KnowledgeRecord], bool | None] | None = None,
    *,
    usage_now: str | None = None,
) -> dict[str, Any]:
    """
    Run janitor scan: evaluate records and persist lifecycle events.

    Args:
        memory_root: Memory root directory
        knowledge_root: Knowledge layer root directory
        config: Janitor configuration
        config_hash: Hash of janitor config
        now: Current timestamp (ISO format). Drives TTL/decay/reactivation
            judgments and the ts recorded on persisted lifecycle events.
        dry_run: If True, compute plan but don't persist events
        source_path_exists: Optional callable to check source path existence
        usage_now: Optional ISO timestamp used as the clock basis for usage
            ledger (offered/read) diagnostics only — everything else keeps
            using `now`. Defaults to real wall-clock time (the janitor's own
            read time), not `now`. #70: a dream run freezes a single `now` at
            run-start and hands it unchanged to a janitor pass that may run
            many minutes later (atomize can take 10+ minutes due to a known
            rglob perf issue); any offered/read event written by another
            concurrent agent in that gap has a ts *after* the frozen `now`
            but *before* the janitor pass actually reads the ledger. Judging
            such events against `now` misclassifies them as `future_event` —
            a real diagnostic reserved for genuine clock skew — which
            escalates to a warning and pins the whole dream run at `partial`.
            Defaulting the usage clock to the actual read time fixes that
            false positive while leaving genuine clock skew (ts beyond the
            real read time) diagnosed exactly as before.

    Returns:
        Dict with keys:
            - summary: Dict with scanned/decayed/reactivated/unchanged/skipped counts
            - plan: List of planned events (if dry_run=True)
            - warnings: List of warning strings
    """
    # Load records
    records, warnings = record_source.iter_records(knowledge_root)
    record_skipped = len(warnings)

    # Build lifecycle state (fails closed on a corrupt lifecycle ledger — the
    # ledger is our own core state and must not be folded from bad data).
    lc_state = _build_lc_state(memory_root)

    # Build import index. import.jsonl is an auxiliary reactivation signal, so a
    # corrupt line degrades (skip reactivation) rather than aborting the scan.
    try:
        import_index, import_bad_line_count = _build_import_index(memory_root)
    except ValueError as exc:
        import_index = {}
        import_bad_line_count = 0
        warnings.append(f"import.jsonl unreadable; reactivation signals skipped: {exc}")
    if import_bad_line_count:
        warnings.append(f"skipped {import_bad_line_count} bad line(s)")

    # v5 requirement #9: fold last_read_at into janitor retention (ttl_base =
    # max(captured_at, active_since_ts, valid last_read_at)); only ever
    # applied to active records inside plan_scan, so a read can't reactivate
    # an already-decayed one. Fail-soft/bounded and read-only against the
    # ledger; a nonzero diagnostic is surfaced as a single warning (v5
    # requirement: no zero-value warning noise).
    #
    # #70: the usage-ledger clock basis is intentionally decoupled from `now`
    # here — it defaults to real wall-clock time (when this scan is actually
    # reading the ledger), not the run's frozen `now`. See run_scan's
    # docstring for the concurrent-write false-positive this avoids.
    if usage_now is not None:
        usage_now_dt = usage_ledger.parse_ts(usage_now) or datetime.now(timezone.utc)
    else:
        usage_now_dt = datetime.now(timezone.utc)
    last_read_map_raw, usage_diag, _usage_stats = usage_ledger.collect_usage_reads(
        memory_root, usage_now_dt
    )
    last_read_map = {sid: last_read for sid, (_count, last_read) in last_read_map_raw.items()}
    nonzero_usage_diag = {key: count for key, count in usage_diag.items() if count > 0}
    if nonzero_usage_diag:
        warnings.append(f"usage ledger diagnostics: {nonzero_usage_diag}")

    # Plan scan
    if source_path_exists is None:
        events = rules.plan_scan(
            records, import_index, lc_state, config, now, config_hash,
            last_read_map=last_read_map,
        )
    else:
        events = rules.plan_scan(
            records, import_index, lc_state, config, now, config_hash, source_path_exists,
            last_read_map=last_read_map,
        )
    lint_findings = rules.plan_lint(records)
    for finding in lint_findings:
        warnings.append(f"lint:{finding['rule']}: {finding['path']} (project={finding['project']})")
    
    # Count event types
    decayed_count = sum(1 for e in events if e["event_type"] == "decayed")
    reactivated_count = sum(1 for e in events if e["event_type"] == "reactivation")
    scanned_count = len(records)
    unchanged_count = scanned_count - decayed_count - reactivated_count
    
    summary = {
        "scanned": scanned_count,
        "decayed": decayed_count,
        "reactivated": reactivated_count,
        "unchanged": unchanged_count,
        "skipped": record_skipped,
        "config_hash": config_hash,
        "dry_run": dry_run,
        "lint": {
            "untitled": sum(1 for finding in lint_findings if finding["rule"] == rules.LINT_TITLE_UNTITLED),
            "raw_remote_key": sum(1 for finding in lint_findings if finding["rule"] == rules.LINT_RAW_REMOTE_KEY),
        },
    }
    
    result: dict[str, Any] = {
        "summary": summary,
        "plan": events if dry_run else [],
        "warnings": warnings,
    }
    
    # Persist events if not dry_run
    if not dry_run:
        for event in events:
            _persist_event(memory_root, event)
    
    return result
