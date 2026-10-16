#!/usr/bin/env python3
"""Grok Build PostToolUse hook: 只記 read attribution，不印任何 stdout。

Grok 會讀 PostToolUse 的 stdout，並可能拿它取代模型看到的工具輸出。
本 hook 因此永不 print。只在 read_file / hashline_read 讀到 knowledge 底下的
檔案時，追加一筆 source=read 到 memory_usage.jsonl。

stdin 同時接受 camelCase（toolName、toolInput、sessionId）與 snake_case
（tool_name、tool_input、session_id）。tool_input 可以是物件或 JSON 字串。
路徑鍵依序看 target_file、file_path、path。任何錯誤都不記事件，exit 0。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "grok"
_READ_TOOLS = {"read_file", "hashline_read"}
_PATH_KEYS = ("target_file", "file_path", "path")
_SLICE_FM = re.compile(r"^slice_id:\s*(\S+)", re.MULTILINE)
_PROJECT_FM = re.compile(r"^project:\s*(\S+)", re.MULTILINE)


def _match(text: str, pattern: re.Pattern) -> str:
    found = pattern.search(text or "")
    return found.group(1).strip().strip("'\"") if found else ""


def _tool_name(payload: dict) -> str:
    value = payload.get("tool_name") or payload.get("toolName") or ""
    return value if isinstance(value, str) else ""


def _tool_input(payload: dict) -> dict:
    raw = payload.get("tool_input")
    if raw is None:
        raw = payload.get("toolInput")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _read_path(tool_input: dict) -> str:
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def main() -> int:
    from paulsha_hippo import policy  # memory-consumer: boundary-aware
    from paulsha_hippo.hooks._wakeup_common import (
        log_warn, memory_root, offered_map_path, read_payload,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)
    try:
        if _tool_name(payload) not in _READ_TOOLS:
            return 0
        tool_input = _tool_input(payload)
        fp = _read_path(tool_input)
        if not fp:
            return 0
        path = Path(fp).resolve()
        knowledge = (root / "knowledge").resolve()
        if knowledge not in path.parents or not path.is_file():
            return 0

        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        mpath = offered_map_path(root, TOOL, session_id)
        by_path: dict = {}
        if mpath.exists():
            try:
                by_path = json.loads(mpath.read_text(encoding="utf-8")).get("by_path", {})
            except Exception:
                by_path = {}

        candidates = [str(fp), str(Path(fp)), str(path)]
        sl_id_offered = next((by_path[c] for c in candidates if c in by_path), "")
        offered = bool(sl_id_offered)

        head = ""
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception:
            head = ""
        project = _match(head, _PROJECT_FM)
        try:
            head = policy.check_boundary(
                "external_to_raw", head,
                project_slug=project or "_unknown", session_ref=session_id,
            ).text
        except Exception:
            pass

        sl_id = sl_id_offered or _match(head, _SLICE_FM)
        project = project or _match(head, _PROJECT_FM)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "tool": TOOL,
            "project": project,
            "sl_id": sl_id,
            "path": str(path),
            "source": "read",
            "offered": offered,
        }
        led_dir = root / "runtime" / "ledger"
        led_dir.mkdir(parents=True, exist_ok=True)
        with (led_dir / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        log_warn(root, TOOL, f"post_tool_use failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
