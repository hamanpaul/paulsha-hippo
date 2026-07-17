#!/usr/bin/env python3
"""Claude Code SessionEnd hook.

Reads stdin JSON (Claude SessionEnd payload), writes an atomic queue payload to
runtime/queue/claude-code__<session-id>.json, then best-effort triggers the
importer in the background.

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

TOOL = "claude-code"


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
    """Replace path separators and colons with __."""
    import re
    return re.sub(r"[/\\:]+", "__", value)


def _fire_importer(root: Path, queue_path: Path) -> None:
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    configured_python = Path(os.environ.get("HIPPO_HOOK_PYTHON", ""))
    importer_python = venv_python if venv_python.exists() else configured_python
    if not importer_python.is_absolute() or not importer_python.is_file() or not os.access(importer_python, os.X_OK):
        _log_warn(root, "hook importer interpreter unavailable; queue written but importer not triggered")
        return
    try:
        subprocess.Popen(
            [
                str(importer_python), "-m", "paulsha_hippo.importer.cli",
                "ingest", "--queue-item", str(queue_path),
                "--memory-root", str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        _log_warn(root, f"importer trigger failed: {exc}")


def main() -> int:
    # #7 自捕捉防護：hippo 自發蒸餾（agent_exec 注入 HIPPO_SELF_SESSION）
    # 的 agent session 不得再被截取，否則遞迴汙染。先於任何 package import。
    import os as _self_os
    if _self_os.environ.get("HIPPO_SELF_SESSION", "").strip():
        return 0
    root = _memory_root()
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
        queue_payload["capture_scope"] = "session_end"
        capture_id = uuid.uuid4().hex
        queue_payload["capture_id"] = capture_id
        # ended_at: prefer explicit field, else timestamp
        if not queue_payload.get("ended_at"):
            ts = queue_payload.get("timestamp")
            if ts:
                queue_payload["ended_at"] = ts

        queue_dir = root / "runtime" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{TOOL}__{_sanitize_id(session_id)}__{capture_id}.json"
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
