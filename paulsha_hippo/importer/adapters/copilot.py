"""GitHub Copilot CLI sessionEnd payload adapter."""

from __future__ import annotations

import time
from pathlib import Path

from paulsha_hippo import paths

from .base import (
    AdapterResult,
    build_session,
    read_copilot_history,
    read_payload,
    string_or_empty,
    string_or_none,
)


_SESSION_END_FLUSH_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4)


def _has_conversation(extracted: dict[str, object]) -> bool:
    return bool(extracted.get("user_prompts") or extracted.get("assistant_messages"))


def _read_session_end_history(config_root: object, session_id: str) -> dict[str, object]:
    """Bounded retry for Copilot's sessionEnd-before-events-flush ordering."""
    extracted = read_copilot_history(config_root, session_id)
    for delay in _SESSION_END_FLUSH_RETRY_DELAYS:
        if _has_conversation(extracted):
            break
        time.sleep(delay)
        extracted = read_copilot_history(config_root, session_id)
    return extracted


def extract(queue_path: str | Path) -> AdapterResult:
    payload = read_payload(queue_path)
    session_id = string_or_empty(payload.get("sessionId")) or string_or_empty(payload.get("session_id"))
    config_root = (
        payload.get("psc_config_root")
        or payload.get("PSC_CONFIG_ROOT")
        or str(paths.copilot_root())
    )
    extract_from = payload
    if session_id:
        if str(payload.get("capture_scope") or "session_end") == "session_end":
            history = _read_session_end_history(config_root, session_id)
        else:
            history = read_copilot_history(config_root, session_id)
        extracted = {k: v for k, v in history.items() if v}
        if extracted:
            extract_from = {**payload, **extracted}
    return build_session(
        payload=extract_from,
        raw_payload=payload,
        queue_path=queue_path,
        tool="copilot-cli",
        session_id=session_id,
        default_capture_scope="session_end",
        ended_at=string_or_none(payload.get("timestamp")) or string_or_none(payload.get("ended_at")),
    )
