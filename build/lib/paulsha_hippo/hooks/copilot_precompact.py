#!/usr/bin/env python3
"""GitHub Copilot CLI preCompact hook.

Reads stdin camelCase JSON (Copilot preCompact payload), writes an atomic queue
payload to runtime/queue/copilot-cli__<session-id>.json with
capture_scope=pre_compact, then best-effort triggers the importer in the background.

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "copilot-cli"


def main() -> int:
    from paulsha_hippo.hooks._wakeup_common import (
        fire_importer,
        log_warn,
        memory_root,
        read_payload,
        write_queue_payload,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)

    if not payload:
        return 0

    try:
        # Normalize camelCase session_id
        session_id = str(
            payload.get("sessionId") or payload.get("session_id") or "unknown"
        )
        # Normalize session_id key in payload
        payload["session_id"] = session_id
        payload.pop("sessionId", None)

        queue_path = write_queue_payload(
            root, TOOL, session_id, payload, capture_scope="pre_compact"
        )
        if queue_path:
            fire_importer(root, TOOL, queue_path)
    except Exception as exc:
        log_warn(root, TOOL, f"failed to write queue or fire importer: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
