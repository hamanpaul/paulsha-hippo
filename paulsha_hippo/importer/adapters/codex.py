"""Codex Stop/SubagentStop payload adapter."""

from __future__ import annotations

from pathlib import Path

from .base import (
    AdapterResult,
    build_session,
    read_codex_rollout,
    read_payload,
    string_or_empty,
    string_or_none,
)


def extract(queue_path: str | Path) -> AdapterResult:
    payload = read_payload(queue_path)
    enrich: dict = {}
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        enrich.update({k: v for k, v in read_codex_rollout(transcript_path).items() if v})
    last = payload.get("last_assistant_message")
    if isinstance(last, str) and last.strip():
        messages = list(enrich.get("assistant_messages") or [])
        if not messages or messages[-1] != last:
            messages.append(last)
        enrich["assistant_messages"] = messages
        enrich["assistant_summary"] = last
    extract_from = {**payload, **enrich} if enrich else payload
    return build_session(
        payload=extract_from,
        raw_payload=payload,
        queue_path=queue_path,
        tool="codex",
        session_id=string_or_empty(payload.get("session_id")),
        default_capture_scope="turn",
        ended_at=string_or_none(payload.get("ended_at")) or string_or_none(payload.get("timestamp")),
    )
