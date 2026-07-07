#!/usr/bin/env python3
"""GitHub Copilot CLI sessionEnd hook.

Reads stdin camelCase JSON (Copilot sessionEnd payload), supplements missing
fields from ~/.copilot/history-session-state/session_<sid>_*.json when
available, writes an atomic queue payload to
runtime/queue/copilot-cli__<session-id>.json, then best-effort triggers the
importer in the background.

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
Config root (for history files): PSC_CONFIG_ROOT env var (default ~).
Any exception is logged to log/hooks.log and the script exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOL = "copilot-cli"


def _memory_root() -> Path:
    env = os.environ.get("PSC_MEMORY_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agents" / "memory"


def _config_root() -> Path:
    env = os.environ.get("PSC_CONFIG_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.home()


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


def _supplement_from_history(payload: dict, session_id: str, config_root: Path) -> dict:
    """Merge fields from the latest matching history file into payload."""
    history_dir = config_root / ".copilot" / "history-session-state"
    if not history_dir.is_dir():
        return payload

    pattern = f"session_{session_id}_*.json"
    candidates = sorted(history_dir.glob(pattern))
    if not candidates:
        return payload

    # Use last (lexicographically latest) matching file
    history_file = candidates[-1]
    try:
        history_data = json.loads(history_file.read_text(encoding="utf-8"))
    except Exception:
        return payload

    merged = dict(history_data)
    # stdin payload takes precedence for fields it provides
    merged.update(payload)
    return merged


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


def main() -> int:
    root = _memory_root()
    cfg_root = _config_root()

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
        # Normalize camelCase session_id
        session_id = str(
            payload.get("sessionId") or payload.get("session_id") or "unknown"
        )

        # Supplement missing fields from history file (best-effort)
        payload = _supplement_from_history(payload, session_id, cfg_root)

        queue_payload = dict(payload)
        queue_payload["tool"] = TOOL
        # Normalize session_id key
        queue_payload["session_id"] = session_id
        queue_payload.pop("sessionId", None)
        # capture_scope: prefer explicit, else session_end
        if not queue_payload.get("capture_scope"):
            queue_payload["capture_scope"] = "session_end"

        queue_dir = root / "runtime" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{TOOL}__{_sanitize_id(session_id)}.json"
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
