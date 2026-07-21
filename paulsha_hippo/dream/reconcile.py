"""Dream reconcile: diagnose and fix _slices ↔ processing ledger desync.

Task 8.3 (dry-run) + 8.4 (apply) of issue-34-atomization-release §8.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..atomizer.pipeline import _archive_fragments, _read_fragment
from ..ledger import dream as dream_ledger
from ..ledger import processing
from . import lock as dream_lock

LOGGER = logging.getLogger(__name__)

_RECONCILE_CONFIG_HASH = "reconcile"


def _scan_fragments(memory_root: Path) -> dict[str, list[Path]]:
    """Scan inbox/_slices for fragments, group by session_key."""
    slices_dir = memory_root / "inbox" / "_slices"
    if not slices_dir.exists():
        return {}
    sessions: dict[str, list[Path]] = {}
    for frag_path in sorted(slices_dir.rglob("*.md")):
        fragment = _read_fragment(frag_path)
        if fragment is None:
            sessions.setdefault("__malformed__", []).append(frag_path)
            continue
        session_key = f"{fragment.source_agent}:{fragment.source_session}"
        sessions.setdefault(session_key, []).append(frag_path)
    return sessions


def _classify(
    frag_sessions: dict[str, list[Path]],
    ledger_events: dict[str, dict],
) -> list[dict]:
    """Cross-reference fragments vs ledger states. Returns detail entries."""
    details: list[dict] = []
    all_sessions = set(frag_sessions.keys()) | set(ledger_events.keys())
    for session_key in sorted(all_sessions):
        if session_key == "__malformed__":
            for frag_path in frag_sessions[session_key]:
                details.append({
                    "session_key": str(frag_path),
                    "category": "malformed",
                    "fragments": len(frag_sessions[session_key]),
                    "action": "skip",
                })
            continue
        frags = frag_sessions.get(session_key, [])
        event = ledger_events.get(session_key)
        state = str(event.get("state", "")) if event else ""

        if not frags and state == "split":
            details.append({"session_key": session_key, "category": "stale_split",
                            "fragments": 0, "action": "mark_no_findings"})
        elif frags and not event:
            details.append({"session_key": session_key, "category": "orphan_fragment",
                            "fragments": len(frags), "action": "set_split"})
        elif frags and state in {"promoted", "no-findings"}:
            details.append({"session_key": session_key, "category": "terminal_unarchived",
                            "fragments": len(frags), "action": "archive"})
        elif frags and state == "split":
            details.append({"session_key": session_key, "category": "healthy",
                            "fragments": len(frags), "action": "none"})
    return details


def _summary_from_details(details: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {
        "orphan_fragment": 0, "terminal_unarchived": 0,
        "stale_split": 0, "healthy": 0, "malformed": 0,
    }
    for d in details:
        cat = d["category"]
        if cat in summary:
            summary[cat] += 1
    return summary


def _apply_fixes(
    memory_root: Path,
    details: list[dict],
    now: str,
    limit: int | None,
) -> dict[str, int]:
    """Execute fixes per category. Returns {"applied": N, "errors": M, "categories": {...}}."""
    counts: dict[str, int] = {
        "orphan_fragment": 0, "terminal_unarchived": 0, "stale_split": 0,
    }
    errors = 0
    applied = 0
    for d in details:
        cat = d["category"]
        if cat not in counts:
            continue
        if limit is not None and counts[cat] >= limit:
            continue
        session_key = d["session_key"]
        try:
            if cat == "orphan_fragment":
                processing.append_state(
                    memory_root, session_key=session_key, state="split",
                    now=now, config_hash=_RECONCILE_CONFIG_HASH,
                    source="reconcile", fragments=d["fragments"],
                )
            elif cat == "terminal_unarchived":
                agent, _, session = session_key.partition(":")
                frag_paths = sorted(
                    (memory_root / "inbox" / "_slices").rglob(
                        f"{agent}__{session}__*.md"
                    )
                )
                _archive_fragments(memory_root, frag_paths, now)
            elif cat == "stale_split":
                processing.append_state(
                    memory_root, session_key=session_key, state="no-findings",
                    now=now, config_hash=_RECONCILE_CONFIG_HASH,
                    source="reconcile",
                    no_findings_reasons=["fragments missing"],
                )
            counts[cat] += 1
            applied += 1
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("reconcile apply failed for %s: %s", session_key, exc)
            errors += 1
    record = {
        "ts": now,
        "run_id": f"reconcile-{now}",
        "status": "ok" if errors == 0 else "partial",
        "passes": {"reconcile": {"applied": applied, "errors": errors, "categories": counts}},
        "errors": [],
        "dream_config_hash": _RECONCILE_CONFIG_HASH,
        "dry_run": False,
    }
    dream_ledger.append_run(memory_root, record)
    return {"applied": applied, "errors": errors, "categories": counts}


def run_reconcile(
    memory_root: Path,
    *,
    now: str,
    dry_run: bool = True,
    apply: bool = False,
    limit: int | None = None,
) -> str:
    """Run reconciliation. Returns JSON string.

    dry_run: produce report only (default).
    apply: execute fixes.
    limit: max N sessions per category (default unlimited).
    """
    lock_handle = dream_lock.acquire_dream_lock(memory_root)
    if lock_handle is None:
        return json.dumps(
            {"skipped": "dream lock held by another process"}, sort_keys=True,
        )
    try:
        frag_sessions = _scan_fragments(memory_root)
        ledger_events = processing.fold_events(memory_root)
        details = _classify(frag_sessions, ledger_events)

        apply_result = None
        if apply:
            apply_result = _apply_fixes(memory_root, details, now, limit)

        summary = _summary_from_details(details)
        result: dict = {"summary": summary, "details": details}
        if apply_result is not None:
            result["applied"] = apply_result
        return json.dumps(result, sort_keys=True, indent=2)
    finally:
        lock_handle.close()