"""
Processing ledger: session state machine for Stage 2 T3 atomizer/linker.

Deterministic state tracking for split/promoted lifecycle.
Canonical JSON: json.dumps(v, sort_keys=True, separators=(",", ":"))
"""
import fcntl
import json
import os
from pathlib import Path
from typing import Any


VALID_STATES = {"split", "promoted", "skipped", "parked"}
PARKED_FAILURE_CATEGORIES = {"backend_unavailable", "transient", "invalid_output"}
_ERROR_TEXT_MAX_CHARS = 500


def sanitize_error_text(text: str, limit: int = _ERROR_TEXT_MAX_CHARS) -> str:
    """Bounded、去敏的錯誤文字：壓平 whitespace、遮蔽 home 前綴、截斷。

    parked 事件與 dream orchestrator 的 error 欄位共用（契約：≤500 字元、去敏）。
    """
    collapsed = " ".join(str(text).split())
    home = str(Path.home())
    if home and home != "/":
        collapsed = collapsed.replace(home, "~")
    return collapsed[:limit]


class ProcessingLedgerError(Exception):
    """Raised when processing ledger is corrupt or invalid."""
    pass


def processing_path(memory_root: Path) -> Path:
    """Return path to processing.jsonl ledger."""
    return memory_root / "runtime" / "ledger" / "processing.jsonl"


def append_state(
    memory_root: Path,
    *,
    session_key: str,
    state: str,
    now: str,
    config_hash: str,
    **extra: Any
) -> None:
    """
    Append a state transition event to the processing ledger.
    
    Args:
        memory_root: Root path for memory storage
        session_key: Session identifier (e.g., "claude:s1")
        state: State value (must be in VALID_STATES)
        now: ISO timestamp string (injected for determinism)
        config_hash: Hash of atomizer configuration
        **extra: Additional fields to include in event
    
    Raises:
        ValueError: If state is not valid
    """
    if state not in VALID_STATES:
        raise ValueError(f"invalid processing state: {state}")
    if state == "parked" and extra.get("failure_category") not in PARKED_FAILURE_CATEGORIES:
        raise ValueError(
            f"parked event requires failure_category in {sorted(PARKED_FAILURE_CATEGORIES)}"
        )

    ledger_path = processing_path(memory_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build event with canonical field order
    event = {
        "ts": now,
        "session_key": session_key,
        "state": state,
        "atomizer_config_hash": config_hash,
        **extra
    }
    
    # Canonical JSON: sorted keys, compact separators
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    
    # Append with exclusive lock
    with open(ledger_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_events(memory_root: Path) -> list[dict[str, Any]]:
    """
    Read all events from processing ledger.
    
    Args:
        memory_root: Root path for memory storage
    
    Returns:
        List of event dictionaries in file order
    
    Raises:
        ProcessingLedgerError: If ledger contains malformed JSON
    """
    ledger_path = processing_path(memory_root)
    
    if not ledger_path.exists():
        return []
    
    events = []
    with open(ledger_path, "r") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:  # Skip blank lines
                    continue
                
                try:
                    event = json.loads(line)
                    events.append(event)
                except json.JSONDecodeError as e:
                    raise ProcessingLedgerError(
                        f"Malformed JSON at line {line_num}: {e}"
                    ) from e
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    
    return events


def fold_states(memory_root: Path) -> dict[str, str]:
    return {
        session_key: str(event["state"])
        for session_key, event in fold_events(memory_root).items()
        if event.get("state")
    }


def fold_events(memory_root: Path) -> dict[str, dict[str, Any]]:
    """
    Fold events into latest-event map.
    
    Events are sorted by (ts, original_index) so latest timestamp wins
    deterministically if timestamps differ, otherwise file order wins.
    
    Args:
        memory_root: Root path for memory storage
    
    Returns:
        Dictionary mapping session_key to the latest event for that session
    """
    events = read_events(memory_root)
    
    # Sort by (ts, original_index) for deterministic latest-timestamp-wins
    indexed_events = [(event, idx) for idx, event in enumerate(events)]
    indexed_events.sort(key=lambda x: (x[0].get("ts", ""), x[1]))
    
    # Fold into latest-event map
    event_map = {}
    for event, _ in indexed_events:
        session_key = event.get("session_key")
        if session_key:
            event_map[session_key] = event
    
    return event_map


def state_of(memory_root: Path, session_key: str) -> str | None:
    """
    Get current state for a session.
    
    Args:
        memory_root: Root path for memory storage
        session_key: Session identifier
    
    Returns:
        Current state string, or None if session not found
    """
    states = fold_states(memory_root)
    return states.get(session_key)
