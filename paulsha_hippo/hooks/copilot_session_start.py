#!/usr/bin/env python3
"""GitHub Copilot CLI sessionStart hook.

Reads stdin camelCase JSON (Copilot sessionStart payload), resolves project from cwd,
builds wake-up brief, and outputs JSON with additionalContext.

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "copilot-cli"


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
        # Normalize camelCase cwd / session id
        cwd = payload.get("cwd") or payload.get("workingDirectory")
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        # capability matrix 2026-07-11 復測：copilot prompt-time hook（userPromptSubmitted）
        # = supported，自動 shortlist 已接線 → 預設 orientation 提示（同 claude）。
        brief = compute_brief_and_record(root, TOOL, session_id, cwd)

        # Copilot sessionStart shape: additionalContext directly
        output = {
            "additionalContext": brief,
        }
        print(json.dumps(output))
    except Exception as exc:
        log_warn(root, TOOL, f"failed to build output: {exc}")
        # Still print minimal valid output on error
        output = {
            "additionalContext": "",
        }
        print(json.dumps(output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
