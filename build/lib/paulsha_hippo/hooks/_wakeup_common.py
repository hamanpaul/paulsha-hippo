#!/usr/bin/env python3
"""Shared helpers for session-start and precompact hooks.

Provides utilities for:
- Memory root resolution
- Logging to hooks.log
- Reading stdin payloads
- Computing wake-up briefs
- Writing queue payloads
- Triggering the importer
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def memory_root() -> Path:
    """Resolve memory root from PSC_MEMORY_ROOT env var or default."""
    env = os.environ.get("PSC_MEMORY_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agents" / "memory"


def log_warn(root: Path, tool: str, msg: str) -> None:
    """Log a warning message to hooks.log."""
    try:
        log_path = root / "log" / "hooks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"WARN {tool}: {msg}\n")
    except Exception:
        pass


def read_payload(root: Path, tool: str) -> dict:
    """Read and parse JSON payload from stdin. Fail-open on errors."""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_warn(root, tool, f"failed to read stdin: {exc}")
        return {}

    try:
        payload: dict = json.loads(raw) if raw.strip() else {}
        return payload
    except json.JSONDecodeError as exc:
        log_warn(root, tool, f"failed to parse stdin JSON: {exc}")
        return {}


def compute_brief(root: Path, cwd: str | None) -> str:
    """Compute wake-up brief for the given cwd.
    
    Returns empty string if project cannot be resolved or brief is empty.
    """
    try:
        from paulsha_hippo.importer.project_resolver import resolve_project
        from paulsha_hippo.wakeup.builder import build_brief
    except ImportError as exc:
        log_warn(root, "wakeup", f"failed to import resolver or builder: {exc}")
        return ""

    try:
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""

        now_iso = datetime.now(timezone.utc).isoformat()
        brief = build_brief(root, project, now=now_iso)
        return brief
    except Exception as exc:
        log_warn(root, "wakeup", f"failed to build brief: {exc}")
        return ""


def sanitize_id(value: str) -> str:
    """Replace path separators and colons with __."""
    return re.sub(r"[/\\:]+", "__", value)


def write_queue_payload(
    root: Path,
    tool: str,
    session_id: str,
    payload: dict,
    capture_scope: str,
) -> Path | None:
    """Write an atomic queue payload to runtime/queue/<tool>__<sid>.json.

    Returns Path on success, or None on failure.
    """
    try:
        queue_payload = dict(payload)
        queue_payload["tool"] = tool
        queue_payload["session_id"] = session_id
        queue_payload["capture_scope"] = capture_scope

        queue_dir = root / "runtime" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{tool}__{sanitize_id(session_id)}.json"
        queue_path = queue_dir / filename
        tmp_path = queue_dir / f".{filename}.tmp"
        tmp_path.write_text(
            json.dumps(queue_payload, sort_keys=True, indent=2), encoding="utf-8"
        )
        tmp_path.replace(queue_path)
        return queue_path
    except Exception as exc:
        log_warn(root, tool, f"failed to write queue: {exc}")
        return None


def fire_importer(root: Path, tool: str, queue_path: Path) -> None:
    """Fire-and-forget trigger the importer in the background."""
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        log_warn(root, tool, f"venv not found at {venv_python}; queue written but importer not triggered")
        return
    try:
        subprocess.Popen(
            [
                str(venv_python), "-m", "paulsha_hippo.importer.cli",
                "ingest", "--queue-item", str(queue_path),
                "--memory-root", str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log_warn(root, tool, f"importer trigger failed: {exc}")


def compute_brief_and_record(root: Path, tool: str, session_id: str, cwd: str | None) -> str:
    """SessionStart 極簡 orientation；不再前置引用前言、不再寫 session-wide offered。"""
    try:
        from paulsha_hippo.importer.project_resolver import resolve_project
        from paulsha_hippo.wakeup.builder import build_orientation
    except ImportError as exc:
        log_warn(root, tool, f"failed to import resolver or builder: {exc}")
        return ""
    try:
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""
        return build_orientation(root, project)
    except Exception as exc:
        log_warn(root, tool, f"failed to build orientation: {exc}")
        return ""
