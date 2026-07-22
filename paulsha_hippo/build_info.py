"""Build and installation identity exposed by the CLI and attestations."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import __version__


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "unknown"


def _packaged_identity(package: Path) -> dict[str, Any]:
    path = package / "_build.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema_version") != "1":
        return {}
    return value


def build_identity(*, package_root: str | Path | None = None, resolve_git: bool = True) -> dict[str, Any]:
    package = Path(package_root).resolve() if package_root else Path(__file__).resolve().parent
    repo_root = package.parent
    commit = os.environ.get("HIPPO_BUILD_COMMIT", "").strip()
    packaged = _packaged_identity(package)
    packaged_version = str(packaged.get("version", "")).strip()
    if packaged_version and packaged_version != __version__:
        raise RuntimeError(
            f"package identity mismatch: runtime={__version__} embedded={packaged_version}"
        )
    if not commit:
        commit = str(packaged.get("build_commit", "")).strip()
    if not commit and resolve_git:
        commit = _git_commit(repo_root)
    source_dirty = bool(packaged.get("source_dirty", False)) or commit.startswith("dirty:")
    return {
        "version": __version__,
        "build_commit": commit or "unknown",
        "source_dirty": source_dirty,
        "install_root": str(repo_root),
        "package_root": str(package),
    }


def version_json(*, package_root: str | Path | None = None) -> str:
    return json.dumps(build_identity(package_root=package_root), sort_keys=True)


__all__ = ["build_identity", "version_json"]
