"""Build hooks for embedding immutable source identity in wheel contents."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


def _source_commit() -> str:
    explicit = os.environ.get("HIPPO_BUILD_COMMIT", "").strip()
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return "unknown"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if status.returncode != 0:
        return "unknown"
    return f"dirty:{value}" if status.stdout.strip() else value


class build_py(_build_py):
    """Write identity only below build_lib; never mutate the source checkout."""

    def run(self) -> None:
        super().run()
        target = Path(self.build_lib) / "paulsha_hippo" / "_build.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        source_commit = _source_commit()
        target.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "build_commit": source_commit,
                    "source_dirty": source_commit.startswith("dirty:"),
                    "version": self.distribution.metadata.version,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


setup(cmdclass={"build_py": build_py})
