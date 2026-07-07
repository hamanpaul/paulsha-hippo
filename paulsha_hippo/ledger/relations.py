"""
Relations ledger for derivation graph — Stage 2 T3 atomizer/linker Task 2.

Manages edges between memory nodes with relation types:
- fragment_of: fragment → session
- promoted_to: fragment → atom
- distilled_from: slice → session
- supersedes: newer → older

All operations use file locking and canonical JSON format.
"""

import fcntl
import json
import os
from pathlib import Path
from typing import Any


VALID_EDGE_TYPES = {
    "fragment_of",
    "promoted_to",
    "distilled_from",
    "supersedes",
    "relates_to",
    "mentions",
}


class RelationsLedgerError(Exception):
    """Exception raised for relations ledger errors."""

    pass


def relations_path(memory_root: Path) -> Path:
    """Return path to relations ledger file."""
    return memory_root / "runtime" / "ledger" / "relations.jsonl"


def append_edge(
    memory_root: Path,
    *,
    type: str,
    frm: str,
    to: str,
    now: str,
    config_hash: str,
) -> None:
    """
    Append an edge to the relations ledger.

    Args:
        memory_root: Root directory for memory storage.
        type: Edge type (must be in VALID_EDGE_TYPES).
        frm: Source node identifier.
        to: Target node identifier.
        now: Timestamp string (injected, not generated).
        config_hash: Atomizer configuration hash.

    Raises:
        ValueError: If edge type is invalid.
    """
    if type not in VALID_EDGE_TYPES:
        raise ValueError(f"invalid relation type: {type}")

    ledger_path = relations_path(memory_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Create edge event
    event = {
        "ts": now,
        "type": type,
        "from": frm,
        "to": to,
        "atomizer_config_hash": config_hash,
    }

    # Write with canonical JSON and exclusive lock
    with open(ledger_path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RelationsLedgerError(
                        f"malformed JSON at line {line_num}: {exc}"
                    ) from exc
                if (
                    existing.get("type"),
                    existing.get("from"),
                    existing.get("to"),
                ) == (type, frm, to):
                    return
            f.seek(0, os.SEEK_END)
            line = json.dumps(event, sort_keys=True, separators=(",", ":"))
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_edges(memory_root: Path) -> list[dict[str, Any]]:
    """
    Read all edges from the relations ledger.

    Args:
        memory_root: Root directory for memory storage.

    Returns:
        List of edge dictionaries.

    Raises:
        RelationsLedgerError: If a line contains malformed JSON.
    """
    ledger_path = relations_path(memory_root)

    if not ledger_path.exists():
        return []

    edges = []
    with open(ledger_path, "r") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    edge = json.loads(line)
                    edges.append(edge)
                except json.JSONDecodeError as e:
                    raise RelationsLedgerError(
                        f"malformed JSON at line {line_num}: {e}"
                    )
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    return edges


def neighbors(memory_root: Path, node: str) -> list[dict[str, Any]]:
    """
    Return all edges connected to the given node.

    Matches edges where the node appears as either 'from' or 'to'.
    Deduplicates identical (type, from, to) edges.

    Args:
        memory_root: Root directory for memory storage.
        node: Node identifier to search for.

    Returns:
        List of unique edge dictionaries connected to the node.
    """
    all_edges = read_edges(memory_root)

    # Filter edges connected to node
    connected = [
        edge for edge in all_edges if edge.get("from") == node or edge.get("to") == node
    ]

    # Deduplicate by (type, from, to)
    seen = set()
    unique = []
    for edge in connected:
        key = (edge.get("type"), edge.get("from"), edge.get("to"))
        if key not in seen:
            seen.add(key)
            unique.append(edge)

    return unique
