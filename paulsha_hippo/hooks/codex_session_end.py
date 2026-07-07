#!/usr/bin/env python3
"""Codex Stop / SubagentStop hook.

Reads stdin JSON (Codex Stop/SubagentStop payload), writes an atomic queue
payload to runtime/queue/codex__<session-id>__<event-id>.json, then best-effort
triggers the importer in the background.

--subagent flag: sets capture_scope=subagent; without the flag capture_scope=turn.
ended_at is always left absent/None for Codex payloads (mid-session snapshots).

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

TOOL = "codex"


def _memory_root() -> Path:
    env = os.environ.get("PSC_MEMORY_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agents" / "memory"


def _log_warn(root: Path, msg: str) -> None:
    try:
        log_path = root / "log" / "hooks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"WARN {TOOL}: {msg}\n")
    except Exception:
        pass


def _sanitize_id(value: str) -> str:
    import re
    return re.sub(r"[/\\:]+", "__", value)


def _fire_importer(root: Path, queue_path: Path) -> None:
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        _log_warn(root, f"venv not found at {venv_python}; queue written but importer not triggered")
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
        _log_warn(root, f"importer trigger failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--subagent", action="store_true")
    args, _ = parser.parse_known_args(argv)

    root = _memory_root()
    capture_scope = "subagent" if args.subagent else "turn"

    try:
        raw = sys.stdin.read()
    except Exception as exc:
        _log_warn(root, f"failed to read stdin: {exc}")
        return 0

    try:
        payload: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _log_warn(root, f"failed to parse stdin JSON: {exc}")
        return 0

    try:
        session_id = str(payload.get("session_id") or "unknown")
        queue_payload = dict(payload)
        queue_payload["tool"] = TOOL
        queue_payload["capture_scope"] = capture_scope
        # Codex Stop/SubagentStop are mid-session snapshots, so ended_at is explicit null.
        queue_payload["ended_at"] = None

        queue_dir = root / "runtime" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{TOOL}__{_sanitize_id(session_id)}__{uuid.uuid4().hex}.json"
        queue_path = queue_dir / filename
        tmp_path = queue_dir / f".{filename}.tmp"
        tmp_path.write_text(
            json.dumps(queue_payload, sort_keys=True, indent=2), encoding="utf-8"
        )
        tmp_path.replace(queue_path)
    except Exception as exc:
        _log_warn(root, f"failed to write queue: {exc}")
        return 0

    _fire_importer(root, queue_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
