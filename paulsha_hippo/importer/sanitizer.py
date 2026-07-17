"""Fail-closed field sanitizer for importer-derived artifacts."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from paulsha_hippo.policy import load_policy, redact_lines

from .adapters.base import NormalizedSession


class SanitizationError(RuntimeError):
    """No derived artifact may be created when sanitization cannot complete."""


@lru_cache(maxsize=1)
def _baseline_policy():
    return load_policy(override_path=None)


def _sanitize_text(value: str, *, session_ref: str) -> str:
    try:
        return redact_lines(
            value,
            policy=_baseline_policy(),
            session_ref=session_ref,
            boundary="raw_to_distilled",
        ).text
    except Exception as exc:  # noqa: BLE001 - fail-closed boundary
        raise SanitizationError("field sanitizer unavailable; derived artifact withheld") from exc


def sanitize_session(session: NormalizedSession) -> NormalizedSession:
    """Return a sanitized copy while leaving the byte-preserved raw payload untouched."""
    sanitized: NormalizedSession = dict(session)
    session_ref = f"{session.get('tool', '')}:{session.get('session_id', '')}"
    for key in ("assistant_summary", "session_title", "cwd", "repo", "commit"):
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = _sanitize_text(value, session_ref=session_ref)  # type: ignore[literal-required]
    for key in ("user_prompts", "assistant_messages", "touched_files", "referenced_artifacts"):
        values = sanitized.get(key)
        if isinstance(values, list):
            sanitized[key] = [  # type: ignore[literal-required]
                _sanitize_text(str(value), session_ref=session_ref) for value in values
            ]
    messages = sanitized.get("assistant_messages") or []
    sanitized["assistant_summary"] = messages[-1] if messages else ""
    return sanitized
