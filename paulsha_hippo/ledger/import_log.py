"""Import log reader for reactivation signals.

This module provides read-only access to import metadata logs
for detecting recently imported records.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO timestamp string for chronological comparisons."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_import_records(import_log_path: Path) -> list[dict]:
    """Read all import records from JSONL log.
    
    Args:
        import_log_path: Path to import.jsonl file
        
    Returns:
        List of import record dicts, or empty list if file doesn't exist
        
    Raises:
        ValueError: If any line contains invalid JSON (with line number context)
    """
    if not import_log_path.exists():
        return []
    
    records = []
    try:
        content = import_log_path.read_text()
    except FileNotFoundError:
        return []
    
    if not content.strip():
        return []
    
    for line_num, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        
        try:
            record = json.loads(line)
            records.append(record)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON at line {line_num}: {e.msg}"
            ) from e
    
    return records


def read_import_records_tolerant(import_log_path: Path) -> tuple[list[dict], int]:
    """Read import records while skipping empty or malformed JSONL rows."""
    if not import_log_path.exists():
        return [], 0

    try:
        content = import_log_path.read_text()
    except FileNotFoundError:
        return [], 0

    if not content.strip():
        return [], 0

    records: list[dict] = []
    bad_line_count = 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            bad_line_count += 1
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad_line_count += 1
            continue

        records.append(record)

    return records, bad_line_count


def recently_imported_record_ids(
    import_log_path: Path,
    *,
    since_ts: str | None = None,
    run_id: str | None = None,
) -> set[str]:
    """Extract record IDs from import log with optional filtering.
    
    Args:
        import_log_path: Path to import.jsonl file
        since_ts: Optional ISO timestamp; only include records with ts >= since_ts
        run_id: Optional run_id; only include records matching this run_id
        
    Returns:
        Set of record IDs from matching import records.
        Extracts from "record_id" field if that key exists, otherwise from "id" field.
        Ignores records without either field or with empty/None/whitespace-only ID values.
        If record_id exists but is not usable, it does not fall back to id.

    Raises:
        ValueError: If since_ts is provided but is not a valid ISO timestamp string.
    """
    records = read_import_records(import_log_path)
    since_dt = None
    if since_ts is not None:
        since_dt = _parse_ts(since_ts)
        if since_dt is None:
            raise ValueError(f"Invalid since_ts: {since_ts!r}")
    
    result = set()
    
    for record in records:
        # Apply run_id filter
        if run_id is not None:
            if record.get("run_id") != run_id:
                continue
        
        # Apply since_ts filter using chronological timestamp comparison.
        if since_dt is not None:
            record_ts = record.get("ts")
            record_dt = _parse_ts(record_ts) if isinstance(record_ts, str) else None
            if record_dt is None or record_dt < since_dt:
                continue
        
        # Extract record ID by key presence so an invalid record_id does not
        # silently reactivate a different id field from the same row.
        if "record_id" in record:
            record_id = record["record_id"]
        elif "id" in record:
            record_id = record["id"]
        else:
            continue

        normalized_record_id = str(record_id).strip() if record_id is not None else ""
        if normalized_record_id:
            result.add(normalized_record_id)
    
    return result
