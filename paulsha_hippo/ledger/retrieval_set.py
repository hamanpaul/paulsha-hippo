"""
Active retrieval set API for memory records.

Provides read-only query interface to determine which records are currently
retrievable based on their lifecycle state.
"""

from pathlib import Path
from typing import List

from paulsha_hippo.ledger.lifecycle import LifecycleEvent, fold_lifecycle, read_events


def active_record_ids(lifecycle_path: Path) -> set[str]:
    """
    Return set of record IDs that are currently retrievable.
    
    A record is retrievable if:
    - Its last_state is 'active'
    - It has not been deleted
    
    This excludes archived, superseded, and deleted records.
    
    Args:
        lifecycle_path: Path to lifecycle.jsonl file
    
    Returns:
        Set of record_id strings for active records
    """
    events = read_events(lifecycle_path)
    
    if not events:
        return set()
    
    lifecycle_state = fold_lifecycle(events)
    
    active_ids = set()
    for record_id, state in lifecycle_state.items():
        if state["last_state"] == "active" and state["deleted"] is not True:
            active_ids.add(record_id)
    
    return active_ids


def active_records(
    memory_root: Path,
    record_ids: List[str],
    *,
    events: List[LifecycleEvent] | None = None,
) -> List[str]:
    """
    Filter list of record IDs to only those currently active.
    
    Records are active if:
    - They have no lifecycle events (default state is active), OR
    - Their last_state is 'active' and not deleted
    
    Args:
        memory_root: Memory root directory (reads runtime/ledger/lifecycle.jsonl)
        record_ids: List of record IDs to check
    
    Returns:
        Sublist of record_ids that are active, preserving order
    """
    lifecycle_path = memory_root / "runtime" / "ledger" / "lifecycle.jsonl"
    lifecycle_events = events if events is not None else read_events(lifecycle_path)

    if not lifecycle_events:
        # No lifecycle events means all records are in default active state
        return record_ids

    lifecycle_state = fold_lifecycle(lifecycle_events)
    
    result = []
    for rid in record_ids:
        if rid not in lifecycle_state:
            # Record has no lifecycle events, default to active
            result.append(rid)
        else:
            state = lifecycle_state[rid]
            if state["last_state"] == "active" and state["deleted"] is not True:
                result.append(rid)
    
    return result


def record_state(memory_root: Path, record_id: str) -> str:
    """
    Get current lifecycle state of a record.
    
    Args:
        memory_root: Memory root directory
        record_id: Record ID to query
    
    Returns:
        State string: 'active', 'decayed', 'archived', 'superseded', or 'unknown'
    """
    events = read_events(memory_root)
    
    if not events:
        return "unknown"
    
    lifecycle_state = fold_lifecycle(events)
    
    if record_id not in lifecycle_state:
        return "unknown"
    
    return lifecycle_state[record_id]["last_state"]
