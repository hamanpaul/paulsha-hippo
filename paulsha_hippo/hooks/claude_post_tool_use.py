#!/usr/bin/env python3
"""Claude Code PostToolUse(Read) hook: record read-based memory usage attribution.

memory-consumer: when a Read targets a path under the memory knowledge layer, append
a `used` event (source="read", offered=bool) to memory_usage.jsonl. The slice content
read is passed through policy.check_boundary (best-effort). Any error -> no event, exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "claude-code"
_SLICE_FM = re.compile(r"^slice_id:\s*(\S+)", re.MULTILINE)
_PROJECT_FM = re.compile(r"^project:\s*(\S+)", re.MULTILINE)


def _match(text: str, pattern: re.Pattern) -> str:
    m = pattern.search(text or "")
    return m.group(1).strip().strip("'\"") if m else ""


def main() -> int:
    from paulsha_hippo import policy  # memory-consumer: boundary-aware
    from paulsha_hippo.hooks._wakeup_common import (
        log_warn, memory_root, read_payload, sanitize_id,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)
    try:
        if payload.get("tool_name") != "Read":
            return 0
        fp = (payload.get("tool_input") or {}).get("file_path")
        if not fp:
            return 0
        p = Path(fp).resolve()
        knowledge = (root / "knowledge").resolve()
        if knowledge not in p.parents:
            return 0

        session_id = str(payload.get("session_id") or "unknown")
        mpath = root / "runtime" / "wakeup" / f"{TOOL}__{sanitize_id(session_id)}.offered.json"
        by_path: dict = {}
        if mpath.exists():
            try:
                by_path = json.loads(mpath.read_text(encoding="utf-8")).get("by_path", {})
            except Exception:
                by_path = {}

        # Offered-map keys are the verbatim shortlist paths (as build_index stored them,
        # i.e. the un-resolved memory_root path). The agent Reads that exact string, but a
        # symlinked memory root means str(p) (resolved) differs — so match raw / normalized
        # / resolved candidates, else genuinely-offered reads mis-record offered=False.
        candidates = [str(fp), str(Path(fp)), str(p)]
        sl_id_offered = next((by_path[c] for c in candidates if c in by_path), "")
        offered = bool(sl_id_offered)

        head = ""
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception:
            head = ""
        project = _match(head, _PROJECT_FM)
        # Boundary-check the slice content we read (best-effort; content is already
        # distilled upstream, so on failure we proceed rather than drop attribution).
        try:
            head = policy.check_boundary(
                "external_to_raw", head,
                project_slug=project or "_unknown", session_ref=session_id,
            ).text
        except Exception:
            pass

        sl_id = sl_id_offered or _match(head, _SLICE_FM)
        project = project or _match(head, _PROJECT_FM)

        ev = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
              "tool": TOOL, "project": project, "sl_id": sl_id, "path": str(p),
              "source": "read", "offered": offered}
        led_dir = root / "runtime" / "ledger"
        led_dir.mkdir(parents=True, exist_ok=True)
        with (led_dir / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception as exc:
        log_warn(root, TOOL, f"post_tool_use failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
