"""Shared usage-ledger streaming reader and bounded diagnostics (issue #41 v5).

Centralizes the line-by-line JSONL parsing rules that ``paulsha_hippo/cli.py``
(usage/funnel commands), ``paulsha_hippo/moc/search.py`` (usage-boost
aggregation for ranking) and ``paulsha_hippo/janitor/scanner.py`` (retention
``last_read_at``) all apply against ``runtime/ledger/offered.jsonl`` and
``runtime/ledger/memory_usage.jsonl``, so the three surfaces share one
fail-soft, bounded-diagnostic, identity/time-window implementation instead of
three drifting copies.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# Fixed, bounded diagnostic key set (v5 BLOCKER #2) — every reason a ledger
# line/event fails to parse or cross-match maps to exactly one of these keys;
# the key set itself never grows, so counters stay bounded regardless of how
# many distinct malformed inputs are seen.
DIAG_KEYS = (
    "io_error",
    "utf8_decode_error",
    "json_decode_error",
    "non_object",
    "missing_tool",
    "missing_session",
    "missing_sl_id",
    "invalid_ts",
    "future_event",
    "window_older",
    "read_without_offered",
)

# Usage-boost read_count/last_read_at aggregation only considers reads within
# this many days of "now" — older reads still exist in the ledger but do not
# feed the search ranking boost or the janitor retention base.
USAGE_BOOST_WINDOW_DAYS = 30

USAGE_BOOST_CAP = 0.04
USAGE_BOOST_COEFFICIENT = 0.01


def new_diagnostics() -> dict[str, int]:
    """A fresh all-zero counter dict over the fixed :data:`DIAG_KEYS` set."""
    return {key: 0 for key in DIAG_KEYS}


def iter_ledger_events(path: Path, diagnostics: dict[str, int]) -> "Iterator[dict]":
    """Yield JSON-object events from ``path`` one line at a time.

    v5 BLOCKER #1/#4: never materializes the file as a single string/list
    (no ``Path.read_text().splitlines()``) and never raises — a missing file,
    an ``OSError``/``UnicodeDecodeError`` while opening or reading, or a
    malformed/non-object line are all fail-soft: the ledger is only ever
    opened for reading here, so a bad read can never mutate it, and whatever
    lines were already readable are still yielded before/around the failure.
    """
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return
    except OSError:
        diagnostics["io_error"] += 1
        return
    try:
        while True:
            try:
                raw_line = handle.readline()
            except OSError:
                diagnostics["io_error"] += 1
                return
            if raw_line == b"":
                return
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                diagnostics["utf8_decode_error"] += 1
                continue
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                diagnostics["json_decode_error"] += 1
                continue
            if not isinstance(event, dict):
                diagnostics["non_object"] += 1
                continue
            yield event
    finally:
        try:
            handle.close()
        except OSError:
            pass


def parse_ts(value: object) -> "datetime | None":
    """Parse an ISO-8601 timestamp string; ``None`` for anything else."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def valid_identity(tool: object, session_id: object, slice_id: object) -> bool:
    """v5 BLOCKER #3: ``(unknown)``/blank tool, session or slice id can't identify anything."""
    values = (
        str(tool or "").strip(),
        str(session_id or "").strip(),
        str(slice_id or "").strip(),
    )
    return all(value and value != "(unknown)" for value in values)


def usage_boost(read_count: int) -> float:
    """``min(0.04, 0.01 * log2(1 + read_count))``; 0.0 for non-positive counts."""
    if read_count <= 0:
        return 0.0
    return min(USAGE_BOOST_CAP, USAGE_BOOST_COEFFICIENT * math.log2(1 + read_count))


def _classify_missing_identity(diagnostics: dict[str, int], tool: object,
                                session_id: object, slice_id: object) -> None:
    tool_str = str(tool or "").strip()
    if not tool_str or tool_str == "(unknown)":
        diagnostics["missing_tool"] += 1
    elif not str(session_id or "").strip() or str(session_id).strip() == "(unknown)":
        diagnostics["missing_session"] += 1
    else:
        diagnostics["missing_sl_id"] += 1


def collect_usage_reads(
    memory_root: Path, now: datetime, window_days: int = USAGE_BOOST_WINDOW_DAYS,
) -> "tuple[dict[str, tuple[int, str]], dict[str, int]]":
    """Aggregate per-slice ``(read_count, last_read_at)`` for ranking/retention.

    v5 requirement #2: a read only counts when the same
    ``(tool, session_id, slice_id)`` triple has a prior ``offered`` event, the
    usage event has ``source == "read"`` and ``kind != "applied"``, and the
    read timestamp is both later than the offer and within
    ``[now - window_days, now]``. Missing/``(unknown)`` identity, unparsable
    timestamps, future timestamps, out-of-window timestamps and reads with no
    matching prior offer are excluded and tallied into the bounded
    :data:`DIAG_KEYS` counters instead of raising or silently vanishing.
    Read-only against the ledger; never writes to it.
    """
    led = memory_root / "runtime" / "ledger"
    diagnostics = new_diagnostics()
    window_start = now - timedelta(days=window_days)

    offered_index: dict[tuple[str, str, str], datetime] = {}
    for event in iter_ledger_events(led / "offered.jsonl", diagnostics):
        tool = event.get("tool")
        session_id = event.get("session_id")
        offered_list = event.get("offered")
        if not isinstance(offered_list, list):
            continue
        offer_ts = parse_ts(event.get("ts"))
        if offer_ts is None:
            diagnostics["invalid_ts"] += 1
            continue
        if offer_ts > now:
            diagnostics["future_event"] += 1
            continue
        if offer_ts < window_start:
            diagnostics["window_older"] += 1
            continue
        for item in offered_list:
            slice_id = item.get("sl_id") if isinstance(item, dict) else item
            if not valid_identity(tool, session_id, slice_id):
                _classify_missing_identity(diagnostics, tool, session_id, slice_id)
                continue
            key = (str(tool), str(session_id), str(slice_id))
            prior = offered_index.get(key)
            if prior is None or offer_ts < prior:
                offered_index[key] = offer_ts

    counts: dict[str, int] = {}
    last_read: dict[str, datetime] = {}
    for event in iter_ledger_events(led / "memory_usage.jsonl", diagnostics):
        if event.get("source") != "read" or event.get("kind") == "applied":
            continue
        tool = event.get("tool")
        session_id = event.get("session_id")
        slice_id = event.get("sl_id")
        if not valid_identity(tool, session_id, slice_id):
            _classify_missing_identity(diagnostics, tool, session_id, slice_id)
            continue
        read_ts = parse_ts(event.get("ts"))
        if read_ts is None:
            diagnostics["invalid_ts"] += 1
            continue
        if read_ts > now:
            diagnostics["future_event"] += 1
            continue
        if read_ts < window_start:
            diagnostics["window_older"] += 1
            continue
        key = (str(tool), str(session_id), str(slice_id))
        offer_ts = offered_index.get(key)
        if offer_ts is None or offer_ts >= read_ts:
            diagnostics["read_without_offered"] += 1
            continue
        sid = str(slice_id)
        counts[sid] = counts.get(sid, 0) + 1
        if sid not in last_read or read_ts > last_read[sid]:
            last_read[sid] = read_ts

    result = {
        sid: (count, last_read[sid].isoformat().replace("+00:00", "Z"))
        for sid, count in counts.items()
    }
    return result, diagnostics
