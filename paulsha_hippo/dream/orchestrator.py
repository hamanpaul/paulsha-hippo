"""Dream orchestrator.

Coordinates Stage 2 "dream" passes (atomize + janitor) and appends an
append-only dream run record to the dream ledger.

This module is intentionally orchestration-only: callers inject the pass
entrypoints via callables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from paulsha_hippo.ledger import dream as dream_ledger
from paulsha_hippo.ledger import processing as processing_ledger

_WARNINGS_RECORDED_MAX = 10
_WARNING_TEXT_MAX_CHARS = 500


def _error_entry(exc: Exception) -> dict[str, Any]:
    """#19 錯誤可見性：bounded 訊息（≤500、去敏）＋ errno，不再只存類別名。"""
    errno_value = getattr(exc, "errno", None)
    if errno_value is None and exc.__cause__ is not None:
        errno_value = getattr(exc.__cause__, "errno", None)
    return {
        "error": type(exc).__name__,
        "error_message": processing_ledger.sanitize_error_text(str(exc)),
        "errno": errno_value if isinstance(errno_value, int) else None,
    }


def _run_pass(
    name: str,
    fn: Callable[[], dict[str, Any]],
    passes: dict[str, Any],
    errors: list[str],
) -> bool:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - orchestration boundary
        entry = _error_entry(exc)
        passes[name] = entry
        errors.append(f"{name}:{entry['error']}")
        return False

    summary: dict[str, Any] = {}
    warnings: Any = None
    if isinstance(result, dict):
        warnings = result.get("warnings")
        value = result.get("summary")
        if isinstance(value, dict):
            summary = value
        else:
            summary = {k: v for k, v in result.items() if k != "warnings"}

    if isinstance(warnings, list) and warnings:
        summary = dict(summary)
        summary["warnings"] = [
            str(warning)[:_WARNING_TEXT_MAX_CHARS]
            for warning in warnings[:_WARNINGS_RECORDED_MAX]
        ]
        summary["warnings_total"] = len(warnings)

    passes[name] = summary

    clean = not warnings and not summary.get("skipped")
    return bool(clean)


def run_dream(
    memory_root: Path,
    *,
    atomize_fn: Callable[[], dict[str, Any]],
    janitor_fn: Callable[[], dict[str, Any]],
    moc_fn: Callable[[], dict[str, Any]] | None = None,
    now: str,
    config_hash: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    passes: dict[str, Any] = {}
    errors: list[str] = []

    run_id = f"dream-{now}"

    atomize_clean = _run_pass("atomize", atomize_fn, passes, errors)
    janitor_clean = _run_pass("janitor", janitor_fn, passes, errors)
    moc_clean = True
    if moc_fn is not None:
        moc_clean = _run_pass("moc", moc_fn, passes, errors)

    if errors:
        status = "failed"
    else:
        status = "ok" if (atomize_clean and janitor_clean and moc_clean) else "partial"

    record: dict[str, Any] = {
        "ts": now,
        "run_id": run_id,
        "status": status,
        "passes": passes,
        "errors": errors,
        "dream_config_hash": config_hash,
        "dry_run": dry_run,
    }

    if not dry_run:
        dream_ledger.append_run(memory_root, record)

    return record
