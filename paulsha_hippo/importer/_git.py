from __future__ import annotations
from pathlib import Path
import subprocess
from typing import Optional

_DEFAULT_TIMEOUT = 2


def _run_git(args: list[str], cwd: str | Path | None = None, timeout: int = _DEFAULT_TIMEOUT) -> Optional[str]:
    try:
        proc = subprocess.run(["git", *args], cwd=(None if cwd is None else str(cwd)), capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return proc.stdout.strip()
        return None
    except Exception:
        return None


def git_toplevel(cwd: str | Path | None) -> Optional[str]:
    """Return repository top-level path for cwd, or None on failure.

    Best-effort, bounded by a short timeout and never raises.
    """
    # Guard against falsy inputs: do not probe current working directory
    if not cwd:
        return None
    try:
        return _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    except Exception:
        return None


def git_remote(toplevel: str | Path | None) -> Optional[str]:
    """Return origin remote URL for the given repository top-level, or None."""
    if not toplevel:
        return None
    try:
        return _run_git(["remote", "get-url", "origin"], cwd=toplevel)
    except Exception:
        return None


def sibling_repo_count(toplevel: str | Path | None) -> int:
    """Count immediate sibling directories of the given toplevel that are git repos.

    Returns 0 on any failure.
    """
    # Guard against falsy inputs: avoid probing parent of current dir
    if not toplevel:
        return 0
    try:
        p = Path(toplevel)
        # If the provided path doesn't exist or isn't a directory, treat as failure
        if not p.exists() or not p.is_dir():
            return 0
        parent = p.parent
        count = 0
        if not parent.exists():
            return 0
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            # Prefer fast, local check: count children that have a .git entry (dir or file)
            git_marker = child / ".git"
            if git_marker.exists():
                count += 1
        return count
    except Exception:
        return 0
