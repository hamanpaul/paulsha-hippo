"""Discover agent-instruction documents and build a verbatim DocCorpus (#147).

The corpus (CLAUDE.md / AGENTS.md / GEMINI.md content) is the reference against
which `noise.classify_noise` recognises doc-fragment slices. Discovery is bounded
(depth-limited walk + skip-list) to stay safe — notably it never descends into
`~/.copilot` (multi-GB; an unbounded scan there has OOM'd WSL before).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

from paulsha_hippo import paths

from .noise import DocCorpus, build_corpus

_DOC_NAMES: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")

# Directories never worth descending for instruction docs: VCS/build/dep dirs,
# the memory store's own derived layers, and known huge agent caches.
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", ".venv", "venv", "env",
    "dist", "build", ".cache", ".obsidian", ".copilot", ".codex-cache",
    # memory store derived/runtime layers (the slices themselves, not docs)
    "archive", "knowledge", "inbox", "runtime",
})


def discover_instruction_docs(
    roots: Iterable[str | Path],
    *,
    max_depth: int = 3,
    doc_names: Sequence[str] = _DOC_NAMES,
    skip_dirs: frozenset[str] = _SKIP_DIRS,
) -> list[Path]:
    """Return instruction-doc paths under ``roots`` (files or dirs), de-duplicated.

    Dirs are walked breadth-bounded to ``max_depth`` levels below the root, with
    ``skip_dirs`` pruned. File roots are accepted directly when their name matches.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    names = set(doc_names)

    def _add(path: Path) -> None:
        rp = path
        if rp not in seen:
            seen.add(rp)
            found.append(rp)

    for raw in roots:
        root = Path(raw)
        if root.is_file():
            if root.name in names:
                _add(root)
            continue
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            depth = len(here.parts) - base
            if depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in names:
                if name in filenames:
                    _add(here / name)
    return found


def default_roots() -> list[Path]:
    """Curated, bounded set of locations where agent-instruction docs live."""
    home = paths.home_root()
    roots = [
        home / ".claude" / "CLAUDE.md",
        home / "CLAUDE.md",
        home / "AGENTS.md",
        home / "GEMINI.md",
        home / ".codex",
        paths.agents_root(),
        home / ".gemini",
        home / "prj_pri",
    ]
    # 額外工作樹 corpus root 由 env 提供（去識別化：不硬編個人/雇主目錄名）。
    extra = paths.extra_corpus_root()
    if extra:
        roots.append(extra)
    return roots


def load_corpus(
    roots: Iterable[str | Path] | None = None,
    *,
    max_depth: int = 3,
) -> DocCorpus:
    """Discover instruction docs under ``roots`` (default: curated locations) and
    build their verbatim DocCorpus. Unreadable docs are skipped, not fatal."""
    use_roots = list(roots) if roots is not None else default_roots()
    texts: list[str] = []
    for path in discover_instruction_docs(use_roots, max_depth=max_depth):
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return build_corpus(texts)


def corpus_for_roots(roots: Iterable[str | Path] | None) -> DocCorpus:
    """Opt-in corpus for CLI/producer ``--instruction-root`` values: falsy roots
    yield an inert (empty) corpus so doc-fragment detection stays off, rather than
    falling back to the broad default scan."""
    if not roots:
        return build_corpus([])
    return load_corpus(list(roots))
