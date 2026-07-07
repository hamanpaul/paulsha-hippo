"""Shared adapter types and tolerant extraction helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict


class NormalizedSession(TypedDict):
    session_id: str
    tool: str
    started_at: str | None
    ended_at: str | None
    cwd: str | None
    repo: str | None
    commit: str | None
    turn_count: int
    user_prompts: list[str]
    assistant_summary: str
    title_source: str
    touched_files: list[str]
    referenced_artifacts: list[str]
    raw_payload_pointer: str


@dataclass(frozen=True)
class AdapterResult:
    session: NormalizedSession
    capture_scope: str
    raw_payload: dict[str, Any]


def read_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        return data
    raise ValueError(f"{path} must contain a top-level JSON object")


def string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def history_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # include common variants and Copilot 'chatMessages'
    for key in ("history", "turns", "messages", "chatMessages"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def extract_user_prompts(payload: dict[str, Any]) -> list[str]:
    explicit = string_list(payload.get("user_prompts"))
    if explicit:
        return explicit
    prompts: list[str] = []
    for item in history_items(payload):
        if item.get("role") != "user":
            continue
        content = item.get("content") or item.get("text") or item.get("prompt")
        if isinstance(content, str):
            prompts.append(content)
    return prompts


def extract_turn_count(payload: dict[str, Any], prompts: list[str]) -> int:
    value = payload.get("turn_count")
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, str):
        try:
            return max(1, int(value))
        except ValueError:
            pass
    items = history_items(payload)
    if items:
        return max(1, len(items))
    return max(1, len(prompts))


def extract_assistant_summary(payload: dict[str, Any]) -> str:
    # check common keys and Copilot/Codex aliases
    for key in ("assistant_summary", "summary", "last_assistant_message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:2000]
    for item in history_items(payload):
        if item.get("role") != "assistant":
            continue
        content = item.get("content") or item.get("text")
        if isinstance(content, str) and content:
            return content[:2000]
    return ""


def build_session(
    *,
    payload: dict[str, Any],
    queue_path: str | Path,
    tool: str,
    session_id: str,
    default_capture_scope: str,
    ended_at: str | None,
    raw_payload: dict[str, Any] | None = None,
) -> AdapterResult:
    prompts = extract_user_prompts(payload)
    session: NormalizedSession = {
        "session_id": session_id,
        "tool": tool,
        "started_at": string_or_none(payload.get("started_at")),
        "ended_at": ended_at,
        "cwd": string_or_none(payload.get("cwd")),
        "repo": string_or_none(payload.get("repo")),
        "commit": string_or_none(payload.get("commit")),
        "turn_count": extract_turn_count(payload, prompts),
        "user_prompts": prompts,
        "assistant_summary": extract_assistant_summary(payload),
        "title_source": string_or_empty(payload.get("title_source")),
        "touched_files": string_list(payload.get("touched_files")),
        "referenced_artifacts": string_list(payload.get("referenced_artifacts")),
        "raw_payload_pointer": str(queue_path),
    }
    capture_scope = string_or_empty(payload.get("capture_scope")) or default_capture_scope
    return AdapterResult(
        session=session,
        capture_scope=capture_scope,
        raw_payload=payload if raw_payload is None else raw_payload,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def read_claude_transcript(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    empty = {"user_prompts": [], "assistant_summary": "", "touched_files": []}
    if not p.exists():
        return empty
    prompts: list[str] = []
    touched: list[str] = []
    last_assistant = ""
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = d.get("type")
        message = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = message.get("content")
        if kind == "user" and isinstance(content, str) and content.strip():
            prompts.append(content)
        elif kind == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                    last_assistant = block["text"]
                elif block.get("type") == "tool_use" and block.get("name") in ("Write", "Edit"):
                    fp = (block.get("input") or {}).get("file_path")
                    if isinstance(fp, str) and fp:
                        touched.append(fp)
    return {"user_prompts": prompts, "assistant_summary": last_assistant, "touched_files": _dedupe(touched)}


def read_copilot_history(config_root: str | Path, session_id: str) -> dict[str, Any]:
    base = Path(config_root)
    if base.name == "history-session-state":
        base_dir = base
    elif base.name == ".copilot":
        base_dir = base / "history-session-state"
    elif base.name == "paulshaclaw" and base.parent.name == ".config":
        base_dir = base.parents[1] / ".copilot" / "history-session-state"
    else:
        base_dir = base / ".copilot" / "history-session-state"
    matches = sorted(base_dir.glob(f"session_{session_id}_*.json")) if base_dir.is_dir() else []
    if not matches:
        return {"user_prompts": [], "assistant_summary": ""}
    try:
        data = json.loads(matches[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"user_prompts": [], "assistant_summary": ""}
    prompts: list[str] = []
    last_assistant = ""
    for m in data.get("chatMessages", []) if isinstance(data, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("content"), str):
            continue
        if m.get("role") == "user":
            prompts.append(m["content"])
        elif m.get("role") == "assistant":
            last_assistant = m["content"]
    return {"user_prompts": prompts, "assistant_summary": last_assistant}


def read_codex_rollout(path: str | Path) -> dict[str, Any]:
    """Best-effort: extract user message text from a codex rollout .jsonl.
    Codex stores turns as 'response_item' records; user turns carry role=='user'
    with a content list of {type:'input_text'|'text', text:str}. Missing/unknown
    shape yields empty prompts (graceful). The assistant summary is NOT read here —
    it comes from the queue payload's 'last_assistant_message' via extract_assistant_summary.
    """
    p = Path(path)
    if not p.exists():
        return {"user_prompts": []}
    prompts: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
        if not isinstance(payload, dict) or payload.get("role") != "user":
            continue
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            prompts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip():
                    prompts.append(block["text"])
    return {"user_prompts": prompts}
