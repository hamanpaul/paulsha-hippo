"""跨 repo 穩定 session 讀取器（lib API）。

入會依據：hippo importer 與 paulshaclaw bro-return hook 兩個使用者；
stdlib-only、自足（#228 對抗審查 F3）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_copilot_history(config_root: str | Path, session_id: str) -> dict[str, Any]:
    base = Path(config_root)
    if base.name in {"history-session-state", "session-state"}:
        copilot_root = base.parent
    elif base.name == ".copilot":
        copilot_root = base
    elif base.name == "paulshaclaw" and base.parent.name == ".config":
        copilot_root = base.parents[1] / ".copilot"
    else:
        copilot_root = base / ".copilot"

    # Current Copilot CLI writes one append-only event stream per session.  Keep
    # the historical aggregate JSON reader below for old installations.
    current_events = copilot_root / "session-state" / session_id / "events.jsonl"
    if current_events.is_file():
        return _read_copilot_events(current_events)
    base_dir = copilot_root / "history-session-state"
    matches = sorted(base_dir.glob(f"session_{session_id}_*.json")) if base_dir.is_dir() else []
    if not matches:
        return {"user_prompts": [], "assistant_messages": [], "assistant_summary": ""}
    try:
        data = json.loads(matches[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"user_prompts": [], "assistant_messages": [], "assistant_summary": ""}
    prompts: list[str] = []
    assistant_messages: list[str] = []
    for m in data.get("chatMessages", []) if isinstance(data, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("content"), str):
            continue
        if m.get("role") == "user":
            prompts.append(m["content"])
        elif m.get("role") == "assistant":
            assistant_messages.append(m["content"])
    return {
        "user_prompts": prompts,
        "assistant_messages": assistant_messages,
        "assistant_summary": assistant_messages[-1] if assistant_messages else "",
    }


def _read_copilot_events(path: Path) -> dict[str, Any]:
    prompts: list[str] = []
    assistant_messages: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        role, content = _copilot_event_payload(event)
        if not content:
            continue
        if role == "user":
            prompts.append(content)
        elif role == "assistant":
            assistant_messages.append(content)
    return {
        "user_prompts": prompts,
        "assistant_messages": assistant_messages,
        "assistant_summary": assistant_messages[-1] if assistant_messages else "",
    }


def _copilot_event_payload(event: dict[str, Any]) -> tuple[str | None, str]:
    """Normalize the observed current event variants without assuming one schema."""
    role = event.get("role")
    event_type = str(event.get("type") or event.get("event") or "").lower()
    if role not in {"user", "assistant"}:
        if "user" in event_type or "prompt" in event_type:
            role = "user"
        elif "assistant" in event_type or "response" in event_type or "message" in event_type:
            role = "assistant"
    payload: Any = event
    for key in ("data", "message", "payload", "content"):
        if isinstance(payload, dict) and key in payload:
            candidate = payload[key]
            if isinstance(candidate, dict):
                payload = candidate
                if role not in {"user", "assistant"}:
                    role = payload.get("role") if payload.get("role") in {"user", "assistant"} else role
            elif isinstance(candidate, str) and key == "content":
                return role, candidate.strip()
    if isinstance(payload, dict):
        content = payload.get("content") or payload.get("text") or payload.get("message")
        if isinstance(content, list):
            content = "\n".join(
                str(block.get("text"))
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
        if isinstance(content, str):
            return role, content.strip()
    return role, ""




def read_codex_rollout(path: str | Path) -> dict[str, Any]:
    """Best-effort: extract user message text from a codex rollout .jsonl.
    Codex stores turns as 'response_item' records; user turns carry role=='user'
    with a content list of {type:'input_text'|'text', text:str}. Assistant messages
    use the same envelope with role=='assistant'. Missing/unknown shape is graceful.
    """
    p = Path(path)
    if not p.exists():
        return {"user_prompts": [], "assistant_messages": [], "assistant_summary": ""}
    prompts: list[str] = []
    assistant_messages: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
        if not isinstance(payload, dict) or payload.get("role") not in {"user", "assistant"}:
            continue
        content = payload.get("content")
        texts: list[str] = []
        if isinstance(content, str) and content.strip():
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip():
                    texts.append(block["text"])
        if payload.get("role") == "user":
            prompts.extend(texts)
        elif texts:
            assistant_messages.append("\n".join(texts))
    return {
        "user_prompts": prompts,
        "assistant_messages": assistant_messages,
        "assistant_summary": assistant_messages[-1] if assistant_messages else "",
    }
