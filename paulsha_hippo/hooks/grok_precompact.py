#!/usr/bin/env python3
"""Grok Build PreCompact hook.

在壓縮前把當下 transcript 指標寫進 queue（capture_scope=pre_compact）。
子 session（subagentType 非空）不截取。不使用 Stop。

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
任何例外記到 log/hooks.log，exit 0。
"""

from __future__ import annotations

import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "grok"


def _is_subagent(payload: dict) -> bool:
    for key in ("subagentType", "subagent_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if value not in (None, "", False):
            return True
    return False


def main() -> int:
    # #7 自捕捉防護：先於任何 package import。
    import os as _self_os
    if _self_os.environ.get("HIPPO_SELF_SESSION", "").strip():
        return 0
    from paulsha_hippo.hooks._wakeup_common import (
        fire_importer,
        log_warn,
        memory_root,
        read_payload,
        write_queue_payload,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)
    if not payload or _is_subagent(payload):
        return 0

    try:
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "unknown")
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
