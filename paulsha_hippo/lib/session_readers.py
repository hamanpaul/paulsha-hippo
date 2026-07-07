"""跨 repo 穩定 session 讀取器（lib API）。

入會依據：hippo importer 與 paulshaclaw bro-return hook 兩個使用者；
stdlib-only、自足（#228 對抗審查 F3）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
