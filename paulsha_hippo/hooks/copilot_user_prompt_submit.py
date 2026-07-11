#!/usr/bin/env python3
"""GitHub Copilot CLI prompt-time hook: inject task-relevant memory shortlist.

僅在 capability matrix（docs/cross-cli-capability-matrix.md）判定 copilot
prompt-time hook = supported 時佈署。事件 key：`userPromptSubmitted`（官方
hooks reference；2026-07-11 probe FIRED、additionalContext 注入實測通過）。
實測 payload：camelCase `{sessionId, timestamp, cwd, prompt}`；additionalContext 直出。
Any error -> empty context, exit 0.
"""
from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "copilot-cli"


def main() -> int:
    from paulsha_hippo.hooks._shortlist_common import build_shortlist_and_record
    from paulsha_hippo.hooks._wakeup_common import log_warn, memory_root, read_payload

    root = memory_root()
    payload = read_payload(root, TOOL)
    context = ""
    try:
        cwd = payload.get("cwd") or payload.get("workingDirectory")
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        prompt = str(payload.get("prompt") or "")
        context = build_shortlist_and_record(root, TOOL, session_id, cwd, prompt)
    except Exception as exc:
        log_warn(root, TOOL, f"user_prompt_submit failed: {exc}")
        context = ""

    print(json.dumps({"additionalContext": context}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
