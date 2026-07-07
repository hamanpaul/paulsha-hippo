#!/usr/bin/env python3
"""Guarded sys.path bootstrap for memory hooks."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_repo_on_path(_hook_file: str | None = None) -> None:
    hook_file = Path(_hook_file).resolve() if _hook_file else Path(__file__).resolve()
    try:
        root = hook_file.parents[2]
    except IndexError:
        return
    if (root / "paulsha_hippo" / "__init__.py").is_file() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
