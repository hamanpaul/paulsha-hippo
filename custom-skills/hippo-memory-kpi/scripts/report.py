#!/usr/bin/env python3
"""Read-only 7/30-day Hippo memory KPI report.

The script deliberately reads ledger/index state directly.  It never invokes
``hippo recall`` and never appends, rebuilds, or repairs any runtime artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paulsha_hippo import __version__  # noqa: E402
from paulsha_hippo.atomizer import slice_frontmatter  # noqa: E402
from paulsha_hippo.build_info import build_identity  # noqa: E402
from paulsha_hippo.moc import census, frontmatter_io  # noqa: E402


SCHEMA_VERSION = "hippo-memory-kpi/v1"
LEDGERS = (
    "import.jsonl",
    "processing.jsonl",
    "publication.jsonl",
    "offered.jsonl",
    "memory_usage.jsonl",
)
SKIP_STATUSES = {"empty-skip", "self-skip", "trivial-skip"}
PROVEN_READ_TOOLS = {"claude-code", "copilot-cli"}


def _diagnostics() -> dict[str, int]:
    return {
        "missing_file": 0,
        "io_error": 0,
        "utf8_decode_error": 0,
        "json_decode_error": 0,
        "non_object": 0,
        "invalid_timestamp": 0,
    }


def _read_jsonl(path: Path, diagnostics: dict[str, int]) -> Iterable[dict[str, Any]]:
    """Stream a JSONL ledger fail-soft without modifying it."""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        diagnostics["missing_file"] += 1
        return
    except OSError:
        diagnostics["io_error"] += 1
        return
    try:
        while True:
            try:
                raw = handle.readline()
            except OSError:
                diagnostics["io_error"] += 1
                return
            if raw == b"":
                return
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                diagnostics["utf8_decode_error"] += 1
                continue
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


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_time(
    event: dict[str, Any],
    diagnostics: dict[str, int],
    *fields: str,
) -> datetime | None:
    for field in fields:
        if field in event:
            value = _parse_time(event.get(field))
            if value is None:
                diagnostics["invalid_timestamp"] += 1
            return value
    diagnostics["invalid_timestamp"] += 1
    return None


def _in_window(value: datetime | None, start: datetime, end: datetime) -> bool:
    return value is not None and start <= value <= end


def _measure(count: int | None, denominator: int) -> dict[str, int | float | None]:
    if count is None or denominator == 0:
        rate: float | None = None
    else:
        rate = round(count * 100 / denominator, 2)
    return {"count": count, "denominator": denominator, "rate_pct": rate}


def _conversion_measure(
    numerator: int, denominator: int
) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate_pct": round(numerator * 100 / denominator, 2) if denominator else None,
    }


def _session_key(event: dict[str, Any]) -> str:
    explicit = event.get("logical_session_key") or event.get("session_key")
    if explicit:
        return str(explicit)
    idempotency = str(event.get("idempotency_key") or "")
    parts = idempotency.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else idempotency


def _usage_session_key(event: dict[str, Any]) -> str:
    tool = str(event.get("tool") or "").strip()
    session_id = str(event.get("session_id") or "").strip()
    return f"{tool}:{session_id}" if tool and session_id else ""


def _fold_processing(
    rows: list[dict[str, Any]],
    now: datetime,
    diagnostics: dict[str, int],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, event in enumerate(rows):
        key = _session_key(event)
        if not key:
            continue
        stamp = _event_time(event, diagnostics, "ts")
        if stamp is None or stamp > now:
            continue
        candidate = (stamp, index, event)
        if key not in latest or candidate[:2] > latest[key][:2]:
            latest[key] = candidate
    return {key: candidate[2] for key, candidate in latest.items()}


def _session_conversion(
    import_rows: list[dict[str, Any]],
    processing: dict[str, dict[str, Any]],
    *,
    start: datetime,
    now: datetime,
    excluded: set[str],
    diagnostics: dict[str, int],
) -> dict[str, Any]:
    latest: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, event in enumerate(import_rows):
        key = _session_key(event)
        if not key or key in excluded:
            continue
        stamp = _event_time(event, diagnostics, "captured_at", "recorded_at")
        if not _in_window(stamp, start, now):
            continue
        candidate = (stamp, index, event)
        if key not in latest or candidate[:2] > latest[key][:2]:
            latest[key] = candidate

    intake = len(latest)
    skip_status_counts: dict[str, int] = defaultdict(int)
    for _, _, event in latest.values():
        status = str(event.get("status") or "")
        if status in SKIP_STATUSES:
            skip_status_counts[status] += 1
    empty_skip = skip_status_counts.get("empty-skip", 0)
    nonempty_keys = {
        key
        for key, (_, _, event) in latest.items()
        if str(event.get("status") or "") not in SKIP_STATUSES
    }
    state_counts = defaultdict(int)
    other_states = defaultdict(int)
    accepted_notes = 0
    for key in nonempty_keys:
        event = processing.get(key, {})
        state = str(event.get("state") or "pending")
        if state in {"promoted", "no-findings", "parked", "pending"}:
            state_counts[state] += 1
        else:
            other_states[state] += 1
        if state == "promoted":
            try:
                accepted_notes += int(event.get("accepted_slices") or 0)
            except (TypeError, ValueError):
                pass

    promoted = state_counts["promoted"]
    parked = state_counts["parked"]
    return {
        "intake_sessions": intake,
        "empty_skip": empty_skip,
        "skipped_sessions": sum(skip_status_counts.values()),
        "skip_status_counts": dict(sorted(skip_status_counts.items())),
        "nonempty_sessions": len(nonempty_keys),
        "promoted": promoted,
        "no_findings": state_counts["no-findings"],
        "parked": parked,
        "pending": state_counts["pending"],
        "other_processing_states": dict(sorted(other_states.items())),
        "accepted_notes": accepted_notes,
        "conversion": _conversion_measure(promoted, len(nonempty_keys)),
        "substantive_conversion": _conversion_measure(promoted, promoted + parked),
    }


def _committed_publications(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    diagnostics: dict[str, int],
) -> set[str]:
    committed: set[str] = set()
    for event in rows:
        if event.get("event") != "publish_commit" or not event.get("publication_id"):
            continue
        stamp = _event_time(event, diagnostics, "now", "ts")
        if stamp is not None and stamp <= now:
            committed.add(str(event["publication_id"]))
    return committed


def _produced_notes(
    rows: list[dict[str, Any]],
    *,
    committed: set[str],
    start: datetime,
    now: datetime,
    excluded: set[str],
    diagnostics: dict[str, int],
) -> dict[str, str]:
    produced: dict[str, str] = {}
    for event in rows:
        if event.get("event") != "publish_prepare":
            continue
        publication_id = str(event.get("publication_id") or "")
        if not publication_id or publication_id not in committed:
            continue
        if _session_key(event) in excluded:
            continue
        stamp = _event_time(event, diagnostics, "now", "ts")
        if not _in_window(stamp, start, now):
            continue
        items = event.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            slice_id = str(item.get("slice_id") or item.get("sl_id") or "").strip()
            if slice_id:
                produced.setdefault(slice_id, str(item.get("target") or ""))
    return produced


def _knowledge_paths(root: Path) -> tuple[dict[str, list[Path]], list[str]]:
    by_id: dict[str, list[Path]] = defaultdict(list)
    problems: list[str] = []
    for entry in census.filesystem_census(root):
        if entry.slice_id:
            by_id[entry.slice_id].append(Path(entry.path))
    for slice_id, paths in by_id.items():
        if len(paths) > 1:
            problems.append(f"duplicate current files for {slice_id}: {len(paths)}")
    return dict(by_id), problems


def _searchable_ids(root: Path) -> tuple[set[str] | None, list[str]]:
    try:
        audit = census.audit_indexed_ids(root)
    except Exception as exc:  # SearchIndexError and corrupt/read failures are report data.
        return None, [str(exc)]
    problems = list(audit.problems)
    if problems:
        return None, problems
    return audit.searchable_ids, problems


def _note_quality(
    produced: dict[str, str],
    *,
    current_paths: dict[str, list[Path]],
    searchable_ids: set[str] | None,
    index_problems: list[str],
) -> dict[str, Any]:
    valid: set[str] = set()
    valid_non_review: set[str] = set()
    review: set[str] = set()
    missing: set[str] = set()
    invalid: set[str] = set()

    for slice_id in produced:
        candidates = current_paths.get(slice_id, [])
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            missing.add(slice_id)
            continue
        try:
            frontmatter, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            invalid.add(slice_id)
            continue
        errors = slice_frontmatter.validate(frontmatter, body)
        if frontmatter.get("slice_id") != slice_id:
            errors.append("slice_id mismatch")
        if errors:
            invalid.add(slice_id)
            continue
        valid.add(slice_id)
        if str(frontmatter.get("artifact_kind") or "").lower() == "review":
            review.add(slice_id)
        else:
            valid_non_review.add(slice_id)

    denominator = len(produced)
    searchable_count = (
        len(set(produced) & searchable_ids) if searchable_ids is not None else None
    )
    adjusted_count = (
        len(valid_non_review & searchable_ids) if searchable_ids is not None else None
    )
    return {
        "produced_unique_notes": denominator,
        "current_files": denominator - len(missing),
        "missing_current": len(missing),
        "invalid_machine": len(invalid),
        "machine_valid": _measure(len(valid), denominator),
        "searchable": _measure(searchable_count, denominator),
        "review_pool_excluded": len(review),
        "adjusted_non_review_searchable": _measure(
            adjusted_count, len(valid_non_review)
        ),
        "index_available": searchable_ids is not None,
        "index_problems": index_problems,
    }


def _offered_ids(event: dict[str, Any]) -> list[str]:
    value = event.get("offered")
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        slice_id = item.get("sl_id") if isinstance(item, dict) else item
        if slice_id:
            result.append(str(slice_id))
    return result


def _usage_metrics(
    offered_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    processing: dict[str, dict[str, Any]],
    *,
    start: datetime,
    now: datetime,
    excluded: set[str],
    offered_diagnostics: dict[str, int],
    usage_diagnostics: dict[str, int],
) -> dict[str, Any]:
    noise = {
        key for key, event in processing.items() if event.get("state") == "no-findings"
    }
    offer_times: dict[tuple[str, str, str], datetime] = {}
    offered_notes: set[str] = set()
    offered_by_tool: dict[str, set[str]] = defaultdict(set)
    for event in offered_rows:
        stamp = _event_time(event, offered_diagnostics, "ts")
        if not _in_window(stamp, start, now):
            continue
        session_key = _usage_session_key(event)
        if not session_key or session_key in excluded or session_key in noise:
            continue
        tool, session_id = session_key.split(":", 1)
        for slice_id in _offered_ids(event):
            key = (tool, session_id, slice_id)
            prior = offer_times.get(key)
            if prior is None or stamp < prior:
                offer_times[key] = stamp
            offered_notes.add(slice_id)
            offered_by_tool[tool].add(slice_id)

    attributed_reads: dict[tuple[str, str, str], datetime] = {}
    viewed_notes: set[str] = set()
    viewed_by_tool: dict[str, set[str]] = defaultdict(set)
    raw_read_events = 0
    raw_read_notes: set[str] = set()
    direct_events = 0
    direct_notes: set[str] = set()
    applied_events: list[tuple[tuple[str, str, str], datetime]] = []
    for event in usage_rows:
        stamp = _event_time(event, usage_diagnostics, "ts")
        if not _in_window(stamp, start, now):
            continue
        session_key = _usage_session_key(event)
        if not session_key or session_key in excluded or session_key in noise:
            continue
        tool, session_id = session_key.split(":", 1)
        if event.get("kind") == "applied":
            slice_id = str(event.get("slice_id") or event.get("sl_id") or "").strip()
            if slice_id:
                applied_events.append(((tool, session_id, slice_id), stamp))
            continue
        if event.get("source") != "read":
            continue
        slice_id = str(event.get("sl_id") or event.get("slice_id") or "").strip()
        if not slice_id:
            continue
        raw_read_events += 1
        raw_read_notes.add(slice_id)
        key = (tool, session_id, slice_id)
        offer_time = offer_times.get(key)
        if offer_time is not None and offer_time < stamp:
            prior = attributed_reads.get(key)
            if prior is None or stamp < prior:
                attributed_reads[key] = stamp
            viewed_notes.add(slice_id)
            viewed_by_tool[tool].add(slice_id)
        else:
            direct_events += 1
            direct_notes.add(slice_id)

    loose_notes: set[str] = set()
    strict_notes: set[str] = set()
    loose_by_tool: dict[str, set[str]] = defaultdict(set)
    strict_by_tool: dict[str, set[str]] = defaultdict(set)
    invalid_applied = 0
    for key, applied_time in applied_events:
        offer_time = offer_times.get(key)
        if offer_time is None or not offer_time < applied_time:
            invalid_applied += 1
            continue
        tool, _, slice_id = key
        loose_notes.add(slice_id)
        loose_by_tool[tool].add(slice_id)
        read_time = attributed_reads.get(key)
        if read_time is not None and read_time < applied_time:
            strict_notes.add(slice_id)
            strict_by_tool[tool].add(slice_id)

    by_tool: dict[str, Any] = {}
    for tool in sorted(offered_by_tool):
        denominator = len(offered_by_tool[tool])
        if tool in PROVEN_READ_TOOLS:
            coverage = "proven-read-hook"
        elif tool == "codex":
            coverage = "lower-bound"
        else:
            coverage = "unknown"
        by_tool[tool] = {
            "offered_unique_notes": denominator,
            "viewed": _measure(len(viewed_by_tool[tool]), denominator),
            "strict_adopted": _measure(len(strict_by_tool[tool]), denominator),
            "loose_offer_applied": _measure(len(loose_by_tool[tool]), denominator),
            "attribution_coverage": coverage,
        }

    denominator = len(offered_notes)
    return {
        "offered_unique_notes": denominator,
        "viewed": _measure(len(viewed_notes), denominator),
        "strict_adopted": _measure(len(strict_notes), denominator),
        "loose_offer_applied": _measure(len(loose_notes), denominator),
        "raw_read_events": raw_read_events,
        "raw_read_unique_notes": len(raw_read_notes),
        "direct_read_events": direct_events,
        "direct_read_unique_notes": len(direct_notes),
        "invalid_applied_events": invalid_applied,
        "by_tool": by_tool,
    }


def _followups_metrics(root: Path, *, start: datetime, now: datetime) -> dict[str, int]:
    """Follow-up ledger 計數（issue #136 fix 5）：open 是 fold 後的目前快照（跨全部
    project，非 window 限定）；resolved_in_source 是本 window 內 resolved-in-source
    事件數。委派給 followups.py 本身的 fold/ledger_path/OPEN_STATES，不在這裡重做
    折疊邏輯；任何例外（模組不存在、ledger 壞檔…）一律 fail-soft 回零，不讓唯讀
    報表因為 follow-up ledger 的問題整份掛掉。"""
    try:
        from paulsha_hippo import followups as fu
        state = fu.fold(root)
        resolved = 0
        ledger_path = fu.ledger_path(root)
        lines = ledger_path.read_text(encoding="utf-8").splitlines() if ledger_path.exists() else []
        for line in lines:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("event") == "resolved-in-source" and _in_window(_parse_time(ev.get("ts")), start, now):
                resolved += 1
        return {
            "open": sum(1 for s in state.values() if s.get("state") in fu.OPEN_STATES),
            "resolved_in_source": resolved,
        }
    except Exception:
        return {"open": 0, "resolved_in_source": 0}


def build_report(
    root: Path,
    *,
    now: datetime,
    days: list[int],
    excluded_sessions: set[str],
) -> dict[str, Any]:
    ledger_root = root / "runtime" / "ledger"
    diagnostics = {name: _diagnostics() for name in LEDGERS}
    rows = {
        name: list(_read_jsonl(ledger_root / name, diagnostics[name]))
        for name in LEDGERS
    }
    processing = _fold_processing(
        rows["processing.jsonl"], now, diagnostics["processing.jsonl"]
    )
    current_paths, path_problems = _knowledge_paths(root)
    searchable_ids, index_problems = _searchable_ids(root)
    all_index_problems = path_problems + index_problems
    if path_problems:
        searchable_ids = None
    committed = _committed_publications(
        rows["publication.jsonl"],
        now=now,
        diagnostics=diagnostics["publication.jsonl"],
    )

    windows: dict[str, Any] = {}
    for window_days in days:
        start = now - timedelta(days=window_days)
        produced = _produced_notes(
            rows["publication.jsonl"],
            committed=committed,
            start=start,
            now=now,
            excluded=excluded_sessions,
            diagnostics=diagnostics["publication.jsonl"],
        )
        windows[f"{window_days}d"] = {
            "days": window_days,
            "since": start.isoformat(),
            "until": now.isoformat(),
            "session_conversion": _session_conversion(
                rows["import.jsonl"],
                processing,
                start=start,
                now=now,
                excluded=excluded_sessions,
                diagnostics=diagnostics["import.jsonl"],
            ),
            "note_quality": _note_quality(
                produced,
                current_paths=current_paths,
                searchable_ids=searchable_ids,
                index_problems=all_index_problems,
            ),
            "usage": _usage_metrics(
                rows["offered.jsonl"],
                rows["memory_usage.jsonl"],
                processing,
                start=start,
                now=now,
                excluded=excluded_sessions,
                offered_diagnostics=diagnostics["offered.jsonl"],
                usage_diagnostics=diagnostics["memory_usage.jsonl"],
            ),
            "followups": _followups_metrics(root, start=start, now=now),
        }

    try:
        identity = build_identity()
    except Exception:
        identity = {"version": __version__, "build_commit": "unknown"}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "memory_root": str(root),
        "memory_root_resolved": str(root.resolve()),
        "reporter_build_identity": identity,
        "excluded_sessions": sorted(excluded_sessions),
        "windows": windows,
        "attribution_limits": {
            "claude-code": "proven-read-hook",
            "copilot-cli": "proven-read-hook",
            "codex": "lower-bound",
            "cortex_source_material": "neither-offer-nor-read",
        },
        "diagnostics": diagnostics,
    }


def _fraction(metric: dict[str, Any]) -> str:
    count = metric.get("count", metric.get("numerator"))
    denominator = metric.get("denominator", 0)
    rate = metric.get("rate_pct")
    if count is None or rate is None:
        return f"n/a/{denominator} (n/a)"
    return f"{count}/{denominator} ({rate:.2f}%)"


def render_markdown(report: dict[str, Any]) -> str:
    windows = report["windows"]
    labels = [f"{payload['days']} 天" for payload in windows.values()]
    lines = [
        "# Hippo memory KPI",
        "",
        f"快照：`{report['generated_at']}`；memory root：`{report['memory_root_resolved']}`",
        "",
        "| 指標 | " + " | ".join(labels) + " | 定義 |",
        "|---|" + "---:|" * len(labels) + "---|",
    ]
    rows = [
        (
            "Session → Atomic note",
            [payload["session_conversion"]["conversion"] for payload in windows.values()],
            "final processing promoted / non-empty intake sessions",
        ),
        (
            "機器可讀 proxy",
            [payload["note_quality"]["machine_valid"] for payload in windows.values()],
            "current file + schema/checksum valid；不是人類語意可讀性",
        ),
        (
            "參考價值 proxy",
            [payload["note_quality"]["searchable"] for payload in windows.values()],
            "searchable / produced unique notes",
        ),
        (
            "調整後參考價值",
            [payload["note_quality"]["adjusted_non_review_searchable"] for payload in windows.values()],
            "searchable / valid non-review notes",
        ),
        (
            "Agent 看過率",
            [payload["usage"]["viewed"] for payload in windows.values()],
            "same tool/session/slice offer → read",
        ),
        (
            "Agent 嚴格採用率",
            [payload["usage"]["strict_adopted"] for payload in windows.values()],
            "same tool/session/slice offer → read → applied",
        ),
    ]
    for name, metrics, definition in rows:
        lines.append(
            f"| {name} | " + " | ".join(_fraction(metric) for metric in metrics) + f" | {definition} |"
        )
    lines.append(
        "| Follow-ups open／resolved(window) | "
        + " | ".join(
            f"{payload['followups']['open']}／{payload['followups']['resolved_in_source']}"
            for payload in windows.values()
        )
        + " | fold 後仍 open（跨全部 project）／window 內 resolved-in-source 事件數 |"
    )

    lines.extend(["", "## 狀態與排除", ""])
    for key, payload in windows.items():
        conversion = payload["session_conversion"]
        usage = payload["usage"]
        lines.append(
            f"- {key}: intake={conversion['intake_sessions']}, empty-skip={conversion['empty_skip']}, "
            f"skipped-total={conversion['skipped_sessions']}, "
            f"nonempty={conversion['nonempty_sessions']}, promoted={conversion['promoted']}, "
            f"no-findings={conversion['no_findings']}, parked={conversion['parked']}, "
            f"pending={conversion['pending']}, other-states={conversion['other_processing_states']}, "
            f"accepted-notes={conversion['accepted_notes']}; "
            f"raw-read-events={usage['raw_read_events']}, "
            f"unattributed-direct-read-events={usage['direct_read_events']}, "
            f"invalid-applied-events={usage['invalid_applied_events']}"
        )
    exclusions = ", ".join(report["excluded_sessions"]) or "(none)"
    lines.append(f"- 排除目前稽核 session：`{exclusions}`。")

    diagnostic_lines: list[str] = []
    for ledger_name, counters in report["diagnostics"].items():
        nonzero = [f"{name}={count}" for name, count in counters.items() if count]
        if nonzero:
            diagnostic_lines.append(f"- `{ledger_name}`: {', '.join(nonzero)}")
    index_problems = sorted(
        {
            problem
            for payload in windows.values()
            for problem in payload["note_quality"]["index_problems"]
        }
    )
    diagnostic_lines.extend(f"- retrieval/index: {problem}" for problem in index_problems[:5])
    if diagnostic_lines:
        lines.extend(
            [
                "",
                "## 資料診斷",
                "",
                *diagnostic_lines,
                "- 診斷非零時，數值只代表成功解析的可觀測子集；不得當作完整母體。",
            ]
        )

    lines.extend(
        [
            "",
            "## Attribution 限制",
            "",
            "- Cortex source_material 直接進 job prompt 屬 neither：不會因此產生 Hippo offer/read。",
            "- Claude Read 與 Copilot view 有已證實的 read hook；Codex read telemetry 目前是可觀測下限。",
            "- `applied` 只作補充時仍要有先行 offer；主採用率固定使用嚴格 offer → read → applied。",
            "- 本報表唯讀，不呼叫 `hippo recall`，避免稽核本身新增 offer 污染分母。",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate read-only Hippo session/note/view/adoption KPIs."
    )
    parser.add_argument(
        "--memory-root",
        default=os.environ.get("PSC_MEMORY_ROOT", str(Path.home() / ".agents" / "memory")),
    )
    parser.add_argument("--days", type=int, action="append", dest="days")
    parser.add_argument("--now", help="ISO-8601 upper bound; defaults to current UTC time")
    parser.add_argument(
        "--exclude-session",
        action="append",
        default=[],
        help="logical tool:session-id to exclude; repeatable",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.memory_root).expanduser()
    if not root.is_dir():
        print(f"hippo-memory-kpi: error: memory root is not a directory: {root}", file=sys.stderr)
        return 2
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("hippo-memory-kpi: error: --now must be ISO-8601", file=sys.stderr)
        return 2
    days = args.days or [7, 30]
    if any(value <= 0 for value in days):
        print("hippo-memory-kpi: error: --days must be positive", file=sys.stderr)
        return 2
    days = list(dict.fromkeys(days))
    report = build_report(
        root,
        now=now,
        days=days,
        excluded_sessions=set(args.exclude_session),
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
