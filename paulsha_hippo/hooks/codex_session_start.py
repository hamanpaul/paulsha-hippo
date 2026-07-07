#!/usr/bin/env python3
"""Codex SessionStart hook.

Reads stdin JSON (Codex SessionStart payload), resolves project from cwd,
builds wake-up brief, and outputs structured JSON with hookSpecificOutput.

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "codex"


def main() -> int:
    from paulsha_hippo.hooks._wakeup_common import (
        compute_brief_and_record,
        log_warn,
        memory_root,
        read_payload,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)

    try:
        session_id = str(payload.get("session_id") or "unknown")
        brief = compute_brief_and_record(root, TOOL, session_id, payload.get("cwd"))

        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": brief,
            }
        }
        print(json.dumps(output))
    except Exception as exc:
        log_warn(root, TOOL, f"failed to build output: {exc}")
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "",
            }
        }
        print(json.dumps(output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
