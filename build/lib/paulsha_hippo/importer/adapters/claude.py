"""Claude Code SessionEnd payload adapter."""

from __future__ import annotations

from pathlib import Path

from .base import (
    AdapterResult,
    build_session,
    read_claude_transcript,
    read_payload,
    string_or_empty,
    string_or_none,
)


def extract(queue_path: str | Path) -> AdapterResult:
    payload = read_payload(queue_path)
    extract_from = payload
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        extracted = {k: v for k, v in read_claude_transcript(transcript_path).items() if v}
        if extracted:
            extract_from = {**payload, **extracted}
    return build_session(
        payload=extract_from,
        raw_payload=payload,
        queue_path=queue_path,
        tool="claude-code",
        session_id=string_or_empty(payload.get("session_id")),
        default_capture_scope="session_end",
        ended_at=string_or_none(payload.get("ended_at")) or string_or_none(payload.get("timestamp")),
    )
