"""
Dream run ledger: append-only JSONL ledger for dream runs.

Minimal, deterministic, flock-protected JSONL writes and reads.
"""
import fcntl
import json
import os
from datetime import datetime, timezone
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


def backlog_census(memory_root: Path, *, now: str | None = None) -> dict[str, Any]:
    """Return one truthful, non-mutating backlog/health census."""
    from . import processing

    folded = processing.fold_events(memory_root)
    states = {
        session_key: str(event.get("state", ""))
        for session_key, event in folded.items()
        if event.get("state")
    }
    raw_paths = []
    inbox = memory_root / "inbox"
    if inbox.exists():
        raw_paths = [
            path for path in inbox.rglob("*.md")
            if "_slices" not in path.relative_to(inbox).parts
        ]
    quarantine = memory_root / "runtime" / "quarantine" / "inbox"
    quarantined = len(list(quarantine.glob("*.md"))) if quarantine.exists() else 0
    reason_counts: dict[str, int] = {}
    for session_key, event in folded.items():
        if event.get("state") == "parked":
            reason = str(event.get("failure_category") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    oldest = None
    oldest_timestamp = None
    for path in raw_paths:
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if oldest_timestamp is None or timestamp < oldest_timestamp:
            oldest_timestamp = timestamp
            oldest = timestamp.isoformat().replace("+00:00", "Z")
    for event in folded.values():
        if event.get("state") not in {"split", "parked"}:
            continue
        raw_timestamp = event.get("ts")
        if not isinstance(raw_timestamp, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if oldest_timestamp is None or timestamp < oldest_timestamp:
            oldest_timestamp = timestamp
            oldest = timestamp.isoformat().replace("+00:00", "Z")
    promoted = sum(state == "promoted" for state in states.values())
    split_sessions = {key for key, state in states.items() if state == "split"}
    retrying_sessions = {
        key for key in split_sessions
        if int(folded.get(key, {}).get("attempts", 0) or 0) > 0
    }
    quarantine_states = sum(state == "quarantined" for state in states.values())
    generic_title = 0
    unknown_project = 0
    invalid_frontmatter = 0
    invalid_checksum = 0
    knowledge = memory_root / "knowledge"
    if knowledge.exists():
        from ..lib.lifecycle.schema import compute_checksum
        from ..moc import frontmatter_io
        from ..noise import is_generic_title

        for path in knowledge.rglob("*.md"):
            try:
                frontmatter, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                invalid_frontmatter += 1
                continue
            required = ("slice_id", "project", "checksum", "memory_layer")
            if not frontmatter or any(field not in frontmatter for field in required):
                invalid_frontmatter += 1
                continue
            if str(frontmatter.get("checksum")) != compute_checksum(body):
                invalid_checksum += 1
            title = frontmatter.get("atom_title") or frontmatter.get("title") or frontmatter.get("session_title")
            generic_title += int(is_generic_title(str(title or "")))
            unknown_project += int(str(frontmatter.get("project") or "") in {"", "_unknown"})
    result = {
        "raw": len(raw_paths),
        "split": len(split_sessions),
        "retrying": len(retrying_sessions),
        "parked": sum(state == "parked" for state in states.values()),
        "quarantined": max(quarantined, quarantine_states),
        "promoted": promoted,
        "generic_title": generic_title,
        "unknown_project": unknown_project,
        "invalid_frontmatter": invalid_frontmatter,
        "invalid_checksum": invalid_checksum,
        "oldest_backlog_at": oldest,
        "oldest_backlog_age_seconds": None,
        "reason_counts": reason_counts,
    }
    if oldest_timestamp is not None and now:
        try:
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            result["oldest_backlog_age_seconds"] = max(0, int((current - oldest_timestamp).total_seconds()))
        except ValueError:
            pass
    return result
