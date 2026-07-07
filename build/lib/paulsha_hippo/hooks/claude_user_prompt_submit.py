#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: inject task-relevant memory shortlist.

Reads stdin JSON (UserPromptSubmit payload), resolves project from cwd, searches
the prompt against the project's bm25 index, and emits a top-k shortlist (title ·
summary · absolute path) as additionalContext. Any error -> empty context, exit 0.
"""
from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "claude-code"


def main() -> int:
    from paulsha_hippo.hooks._shortlist_common import build_shortlist_and_record
    from paulsha_hippo.hooks._wakeup_common import log_warn, memory_root, read_payload

    root = memory_root()
    payload = read_payload(root, TOOL)
    context = ""
    try:
        cwd = payload.get("cwd")
        session_id = str(payload.get("session_id") or "unknown")
        prompt = str(payload.get("prompt") or "")
        context = build_shortlist_and_record(root, TOOL, session_id, cwd, prompt)
    except Exception as exc:
        log_warn(root, TOOL, f"user_prompt_submit failed: {exc}")
        context = ""

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
