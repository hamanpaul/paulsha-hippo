#!/usr/bin/env python3
"""GitHub Copilot CLI postToolUse(view) hook: record read-based memory usage attribution.

memory-consumer: when a `view`（Read file contents）targets a path under the memory
knowledge layer, append a `used` event (source="read", offered=bool) to
memory_usage.jsonl —— claude_post_tool_use.py 的 copilot 對稱面。

實測 payload（2026-07-11 payload probe；官方 hooks reference 同形）：
  {"sessionId": ..., "cwd": ..., "toolName": "view",
   "toolArgs": "{\"path\": \"...\", \"view_range\": [1, 5]}", "toolResult": {...}}
注意 `toolArgs` 是 JSON 字串。工具過濾在腳本內做（toolName != "view" 即早退），
不依賴平台 matcher。Any error -> no event, exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "copilot-cli"
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
        if payload.get("toolName") != "view":
            return 0
        raw_args = payload.get("toolArgs")
        if isinstance(raw_args, str):
            try:
                tool_args = json.loads(raw_args)
            except Exception:
                return 0
        elif isinstance(raw_args, dict):  # 防禦：未來若改為物件形
            tool_args = raw_args
        else:
            return 0
        fp = tool_args.get("path") if isinstance(tool_args, dict) else None
        if not fp:
            return 0
        p = Path(fp).resolve()
        knowledge = (root / "knowledge").resolve()
        if knowledge not in p.parents:
            return 0
        # copilot 的 view 也能列目錄（Claude 的 Read 不會）：目錄不構成 slice read，
        # 記了只會產生空 attribution 噪音事件。
        if not p.is_file():
            return 0

        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        mpath = root / "runtime" / "wakeup" / f"{TOOL}__{sanitize_id(session_id)}.offered.json"
        by_path: dict = {}
        if mpath.exists():
            try:
                by_path = json.loads(mpath.read_text(encoding="utf-8")).get("by_path", {})
            except Exception:
                by_path = {}

        # Offered-map keys are the verbatim shortlist paths (un-resolved memory_root
        # path)。symlinked memory root 下 str(p)（resolved）會不同——比對 raw /
        # normalized / resolved 三個候選，避免真 offered 的 read 記成 offered=False。
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
