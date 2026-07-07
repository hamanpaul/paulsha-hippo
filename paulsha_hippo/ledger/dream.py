"""
Dream run ledger: append-only JSONL ledger for dream runs.

Minimal, deterministic, flock-protected JSONL writes and reads.
"""
import fcntl
import json
import os
from pathlib import Path
from typing import Any


class DreamLedgerError(Exception):
    """Raised when dream ledger is corrupt or invalid."""


def dream_path(memory_root: Path) -> Path:
    """Return path to dream.jsonl ledger."""
    return memory_root / "runtime" / "ledger" / "dream.jsonl"


def append_run(memory_root: Path, record: dict[str, Any]) -> None:
    """Append a run record to the dream ledger using canonical JSONL with flock.

    The record must be provided (including ts) by the caller; this function
    does not generate timestamps.
    """
    # Validate input early: ledger only accepts JSON objects (mappings).
    if not isinstance(record, dict):
        raise TypeError("record must be a mapping (dict)")

    path = dream_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(record, sort_keys=True, separators=(",", ":"))

    with open(path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0, os.SEEK_END)
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_runs(memory_root: Path) -> list[dict[str, Any]]:
    """Read all run records from dream ledger.

    Returns an empty list if the ledger file does not exist. If any line is
    malformed JSON, raises DreamLedgerError (fail-closed).
    """
    path = dream_path(memory_root)
    if not path.exists():
        return []

    runs: list[dict[str, Any]] = []
    with open(path, "r") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as e:
                    raise DreamLedgerError(f"Malformed JSON at line {line_num}: {e}") from e
                if not isinstance(value, dict):
                    raise DreamLedgerError(f"Invalid ledger entry at line {line_num}: expected object")
                runs.append(value)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    return runs


def last_run(memory_root: Path) -> dict[str, Any] | None:
    """Return the last run record or None if none exist."""
    runs = read_runs(memory_root)
    return runs[-1] if runs else None


def backlog_depth(memory_root: Path) -> int:
    """Count raw sessions under inbox/**/*.md excluding inbox/_slices/**.

    Only counts markdown files directly under the inbox tree, excluding any
    file whose first path component under inbox is "_slices".
    """
    inbox = memory_root / "inbox"
    if not inbox.exists():
        return 0

    count = 0
    for p in inbox.rglob("*.md"):
        try:
            rel = p.relative_to(inbox)
        except Exception:
            continue
        # Exclude anything under inbox/_slices/**
        if rel.parts and rel.parts[0] == "_slices":
            continue
        count += 1

    return count
