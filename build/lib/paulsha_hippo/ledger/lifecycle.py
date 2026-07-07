"""
Lifecycle ledger for memory record event tracking.

Event-sourced ledger that tracks all lifecycle events (created, updated, 
accessed, archived, deleted, etc.) for memory records with hash-chained integrity.
"""

import fcntl
import os
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, TypedDict


class LifecycleEvent(TypedDict, total=False):
    """Type definition for lifecycle events."""
    ts: str
    event_id: str
    record_id: str
    event_type: str
    source: str
    reason: str
    actor: str
    run_id: Optional[str]
    seq: int
    metadata: Optional[Dict[str, Any]]
    prev_hash: Optional[str]
    event_hash: str


VALID_EVENT_TYPES = {
    "created",
    "imported",
    "accessed",
    "updated",
    "superseded",
    "archived",
    "restored",
    "deleted",
    "decayed",
    "reactivation",
}


def _canonical_json(obj: Dict[str, Any]) -> str:
    """Return canonical JSON string for hashing (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))


def _compute_event_hash(event: Dict[str, Any]) -> str:
    """Compute SHA256 hash of event payload excluding event_hash field."""
    payload = {k: v for k, v in event.items() if k != "event_hash"}
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _is_moc_reconcile_dedup_event(event: LifecycleEvent) -> bool:
    """Return True for audit-only MOC reconcile dedup traces."""
    metadata = event.get("metadata")
    return (
        event.get("source") == "moc-reconcile"
        and isinstance(metadata, dict)
        and "deleted_path" in metadata
        and "kept_path" in metadata
    )


def _parse_events(lines: Iterable[str]) -> List[LifecycleEvent]:
    events = []
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid lifecycle JSONL at line {line_number}: {exc.msg}") from exc
    return events


def _read_events_from_locked_file(f: TextIO) -> List[LifecycleEvent]:
    f.seek(0)
    return _parse_events(f)


def append_event(
    path: Path,
    record_id: str,
    event_type: str,
    source: str,
    reason: str,
    actor: str,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ts: Optional[str] = None,
) -> LifecycleEvent:
    """
    Append a lifecycle event to the ledger with hash chaining.

    Uses fcntl.flock to serialize writers and ensure atomic appends.
    Computes event_hash as SHA256 of canonical JSON (excluding event_hash itself).
    Chains events via prev_hash pointing to previous event_hash.

    Args:
        path: Path to lifecycle.jsonl file
        record_id: Memory record identifier
        event_type: One of VALID_EVENT_TYPES
        source: System/component that generated the event
        reason: Human-readable reason for the event
        actor: User/agent/process that triggered the event
        run_id: Optional batch/run identifier
        metadata: Optional event-specific metadata dict
        ts: Optional logical event timestamp (ISO8601). When provided it is
            recorded verbatim so a scan's injected ``now`` drives the ledger's
            temporal ordering; falls back to wall-clock when omitted.

    Returns:
        The appended LifecycleEvent dict
    """
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event_type: {event_type}")

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read and append under the same lock so seq and prev_hash stay linear.
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            events = _read_events_from_locked_file(f)
            prev_hash = events[-1]["event_hash"] if events else None
            seq = events[-1]["seq"] + 1 if events else 1

            now = ts if ts else datetime.now(timezone.utc).isoformat()
            event: LifecycleEvent = {
                "ts": now,
                "event_id": f"{now}-{uuid.uuid4()}",
                "record_id": record_id,
                "event_type": event_type,
                "source": source,
                "reason": reason,
                "actor": actor,
                "run_id": run_id,
                "seq": seq,
                "metadata": metadata,
                "prev_hash": prev_hash,
                "event_hash": "",
            }
            event["event_hash"] = _compute_event_hash(event)

            f.seek(0, 2)
            f.write(json.dumps(event) + "\n")
            # Flush the buffered write to the OS (and fsync to disk) BEFORE releasing
            # the lock. Otherwise the data sits in Python's userspace buffer until the
            # file is closed — which happens AFTER the unlock below — leaving a window
            # where another writer can take the lock, read stale (pre-write) state, and
            # reuse the same seq/prev_hash, breaking the hash chain.
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    
    return event


def read_events(path: Path) -> List[LifecycleEvent]:
    """
    Read all lifecycle events from the ledger.
    
    Returns events in file order. Tolerant of missing file (returns empty list).
    
    Args:
        path: Path to lifecycle.jsonl file OR memory_root directory.
              If a directory, reads from runtime/ledger/lifecycle.jsonl
    
    Returns:
        List of LifecycleEvent dicts in file order
    """
    # If path is a directory, resolve to canonical lifecycle ledger
    if path.is_dir():
        path = path / "runtime" / "ledger" / "lifecycle.jsonl"
    
    if not path.exists():
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return _parse_events(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def fold_lifecycle(events: List[LifecycleEvent]) -> Dict[str, Dict[str, Any]]:
    """
    Fold lifecycle events into current state per record_id.
    
    State transitions:
    - created/imported/updated/restored => active
    - accessed => preserves current state, updates last_access_ts
    - superseded => superseded, sets superseded_by from metadata.detail['superseded_by']
    - MOC reconcile dedup traces (source="moc-reconcile") are audit-only and are
      skipped here: they never create or change effective state (see #184)
    - archived => archived, sets archive_reason from reason
    - deleted => deleted, sets deleted flag
    - decayed => decayed
    - reactivation => active
    
    Args:
        events: List of lifecycle events
    
    Returns:
        Dict keyed by record_id with fields:
            - last_state: current lifecycle state
            - last_event_ts: timestamp of most recent state-affecting event
              (audit-only MOC dedup traces do not advance it)
            - last_access_ts: timestamp of most recent access (if any)
            - archive_reason: reason for archival (if archived)
            - superseded_by: record_id that supersedes this one (if superseded)
            - deleted: True if deleted, None otherwise
    """
    result: Dict[str, Dict[str, Any]] = {}
    
    for event in events:
        record_id = event["record_id"]
        event_type = event["event_type"]
        ts = event["ts"]
        # MOC reconcile dedup deletions are an audit-only trace: they live in
        # the raw ledger (read_events) for provenance but MUST NOT establish or
        # change effective lifecycle state. A slice whose only event is this
        # trace stays "unknown"; a slice with a prior state keeps it.
        # (#184 adversarial-review finding: it previously set None -> "active".)
        if _is_moc_reconcile_dedup_event(event):
            continue

        if record_id not in result:
            result[record_id] = {
                "last_state": None,
                "last_event_ts": None,
                "last_access_ts": None,
                "archive_reason": None,
                "superseded_by": None,
                "deleted": None,
            }

        rec = result[record_id]
        rec["last_event_ts"] = ts
        
        if event_type in {"created", "imported", "updated", "restored", "reactivation"}:
            rec["last_state"] = "active"
            rec["deleted"] = None
        elif event_type == "accessed":
            rec["last_access_ts"] = ts
        elif event_type == "superseded":
            metadata = event.get("metadata") or {}
            rec["last_state"] = "superseded"
            detail = metadata.get("detail") or {}
            rec["superseded_by"] = detail.get("superseded_by")
        elif event_type == "archived":
            rec["last_state"] = "archived"
            rec["archive_reason"] = event.get("reason")
        elif event_type == "deleted":
            rec["last_state"] = "deleted"
            rec["deleted"] = True
        elif event_type == "decayed":
            rec["last_state"] = "decayed"
    
    return result
