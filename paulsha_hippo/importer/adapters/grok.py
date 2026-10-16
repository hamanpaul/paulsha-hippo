"""Grok Build chat_history.jsonl adapter.

live transcript 形狀（只取結構，不依賴本機 session 原文）：
- type=user，content 為 [{type:text, text}]；帶 synthetic_reason 的列跳過
- type=assistant，content 為字串，tool_calls[].arguments 可能是 JSON 字串
- type=system / reasoning / tool_result / backend_tool_call 不進 prompt 或摘要

寫入檔路徑只從 write、search_replace、hashline_edit 的
target_file / file_path / path 收集。transcript_path 可以是 jsonl，
或 session 目錄；若指到 events.jsonl，改讀同目錄的 chat_history.jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import (
    AdapterResult,
    build_session,
    read_payload,
    string_or_empty,
    string_or_none,
)

_WRITE_TOOLS = {"write", "search_replace", "hashline_edit"}
_PATH_KEYS = ("target_file", "file_path", "path")


def _empty() -> dict[str, Any]:
    return {
        "user_prompts": [],
        "assistant_messages": [],
        "assistant_summary": "",
        "touched_files": [],
    }


def resolve_grok_transcript(path: str | Path) -> Path | None:
    candidate = Path(path)
    if candidate.is_dir():
        history = candidate / "chat_history.jsonl"
        return history if history.is_file() else None
    if candidate.name == "events.jsonl":
        sibling = candidate.with_name("chat_history.jsonl")
        if sibling.is_file():
            return sibling
    return candidate if candidate.is_file() else None


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str) and block.strip():
            parts.append(block)
        elif (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        ):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [item for item in items if not (item in seen or seen.add(item))]


def _touched_path(arguments: dict[str, Any]) -> str:
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def read_grok_transcript(path: str | Path) -> dict[str, Any]:
    resolved = resolve_grok_transcript(path)
    if resolved is None:
        return _empty()
    prompts: list[str] = []
    assistant_messages: list[str] = []
    touched: list[str] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        if kind == "user":
            if record.get("synthetic_reason"):
                continue
            text = _user_text(record.get("content"))
            if text:
                prompts.append(text)
        elif kind == "assistant":
            content = record.get("content")
            if isinstance(content, str) and content.strip():
                assistant_messages.append(content)
            calls = record.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict) or call.get("name") not in _WRITE_TOOLS:
                    continue
                found = _touched_path(_parse_arguments(call.get("arguments")))
                if found:
                    touched.append(found)
    return {
        "user_prompts": prompts,
        "assistant_messages": assistant_messages,
        "assistant_summary": assistant_messages[-1] if assistant_messages else "",
        "touched_files": _dedupe(touched),
    }


def extract(queue_path: str | Path) -> AdapterResult:
    payload = read_payload(queue_path)
    extract_from = payload
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    if isinstance(transcript_path, str) and transcript_path:
        extracted = {key: value for key, value in read_grok_transcript(transcript_path).items() if value}
        if extracted:
            extract_from = {**payload, **extracted}
    session_id = string_or_empty(payload.get("session_id") or payload.get("sessionId"))
    return build_session(
        payload=extract_from,
        raw_payload=payload,
        queue_path=queue_path,
        tool="grok",
        session_id=session_id,
        default_capture_scope="session_end",
        ended_at=string_or_none(payload.get("ended_at")) or string_or_none(payload.get("timestamp")),
    )
