from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io


# Keep Unicode word chars (letters incl. CJK, digits, underscore); fold every run
# of other chars (punctuation, whitespace, symbols) to a single hyphen. Preserving
# CJK is what stops pure-CJK titles collapsing to "untitled" (#151).
_SLUG_STRIP = re.compile(r"[^\w]+", re.UNICODE)


def slugify(title: str) -> str:
    """Convert title to a slug, preserving CJK/Unicode letters; kebab-case ASCII."""
    slug = _SLUG_STRIP.sub("-", title.strip().lower()).strip("-_")
    return slug or "untitled"


def _title(fm: dict[str, Any], body: str) -> str:
    """Extract title from frontmatter, markdown heading, or fallback."""
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return f"{fm.get('artifact_kind', 'note')}-{fm.get('project', 'unknown')}"


def target_name(fm: dict[str, Any], body: str) -> str:
    """Generate target filename: <slug>--<slice_id>.md"""
    return f"{slugify(_title(fm, body))}--{fm['slice_id']}.md"


def _lifecycle_path(memory_root: Path) -> Path:
    """Get path to the lifecycle ledger."""
    return memory_root / "runtime" / "ledger" / "lifecycle.jsonl"


def _append_superseded_event(
    memory_root: Path,
    slice_id: str,
    deleted_path: Path,
    kept_path: Path,
    ts: str | None = None,
) -> None:
    """Best-effort lifecycle trace for reconcile deletions.

    ``kept_path`` is the file that survives the current unlink decision before
    any follow-up rename happens. ``ts`` carries the moc pass's injected ``now``
    so the ledger event stays deterministic (no wall-clock in the moc pass).
    """
    try:
        lifecycle.append_event(
            path=_lifecycle_path(memory_root),
            record_id=slice_id,
            event_type="superseded",
            source="moc-reconcile",
            reason="moc dedup",
            actor="moc-reconcile",
            run_id=None,
            ts=ts,
            metadata={
                "deleted_path": str(deleted_path),
                "kept_path": str(kept_path),
                "schema_version": "1",
            },
        )
    except Exception:
        pass


def reconcile(memory_root: Path, now: str | None = None) -> list[str]:
    """Rename slices to <title>--<slice_id>.md and dedup by slice_id. Returns warnings.

    ``now`` is the moc pass's injected logical timestamp; it is stamped on the
    dedup lifecycle traces so they stay deterministic (no wall-clock).
    """
    knowledge = memory_root / "knowledge"
    warnings: list[str] = []
    if not knowledge.exists():
        return warnings
    seen: dict[str, Path] = {}
    for path in sorted(knowledge.rglob("*.md")):
        fm, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
        if fm.get("memory_layer") != "knowledge":
            continue
        slice_id = fm.get("slice_id")
        if not slice_id:
            warnings.append(f"{path}: missing slice_id; skipped")
            continue
        target = path.with_name(target_name(fm, body))
        if path != target:
            if target.exists():
                # Only overwrite if current file is newer
                if path.stat().st_mtime <= target.stat().st_mtime:
                    _append_superseded_event(memory_root, slice_id, path, target, ts=now)
                    path.unlink()
                    continue
                _append_superseded_event(memory_root, slice_id, target, path, ts=now)
                target.unlink()
            path.rename(target)
            path = target
        if slice_id in seen:
            other = seen[slice_id]
            if path.resolve() != other.resolve():
                older = other if other.stat().st_mtime <= path.stat().st_mtime else path
                newer = path if older is other else other
                _append_superseded_event(memory_root, slice_id, older, newer, ts=now)
                older.unlink()
                seen[slice_id] = newer
                warnings.append(f"duplicate slice_id {slice_id}; kept {newer.name}")
        else:
            seen[slice_id] = path
    return warnings
