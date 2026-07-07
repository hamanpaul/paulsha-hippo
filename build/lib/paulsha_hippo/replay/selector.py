"""Replay selector for distilled knowledge slices.

Minimal implementation used by Stage2 dream service Task 6.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from paulsha_hippo.ledger import relations, retrieval_set


class SelectorError(Exception):
    pass


def _frontmatter(text: str) -> Dict[str, object]:
    """Parse YAML frontmatter delimited by '---' fence lines into a dict.

    This matches the repository's record_source behavior: require the first
    line to be '---' and the closing '---' to appear on its own line. Only the
    block between those lines is fed to yaml.safe_load(). If parsing isn't
    possible or PyYAML is unavailable, return an empty dict.
    """
    text = text or ""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    block = "\n".join(lines[1:end])
    if not block.strip():
        return {}
    try:
        import yaml  # type: ignore

        try:
            data = yaml.safe_load(block)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}
    except Exception:
        # PyYAML not available; return empty dict per spec
        return {}


def _entity_slice_ids(memory_root: Path, entity: str) -> List[str]:
    node = f"entity:{entity}"
    edges = relations.neighbors(memory_root, node)
    ids: List[str] = []
    for e in edges:
        if e.get("type") != "mentions":
            continue
        frm = e.get("from", "")
        if frm.startswith("slice:"):
            ids.append(frm.split("slice:", 1)[1])
    return ids


def select(
    memory_root: Path,
    *,
    project: Optional[str] = None,
    tags: Optional[List[str]] = None,
    entity: Optional[str] = None,
    include_decayed: bool = False,
) -> List[Path]:
    """Select distilled slice files matching facets.

    At least one facet (project, tags, entity) is required. Facets are AND-composed.
    By default filters decayed records via retrieval_set.active_records().
    Returns list of Path objects to matching slice files under memory_root/knowledge.
    """
    if not (project or tags or entity):
        raise SelectorError("at least one facet required")

    knowledge_root = Path(memory_root) / "knowledge"
    if not knowledge_root.exists():
        return []

    # Discover slice files and parse frontmatter
    slice_map: Dict[str, Path] = {}
    fm_map: Dict[str, Dict[str, object]] = {}
    for p in sorted(knowledge_root.rglob("*.md")):
        # Skip symlinked files to match record_source behavior
        if p.is_symlink():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _frontmatter(text)
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if not sid:
            continue
        sid_str = str(sid)
        if sid_str in slice_map:
            # Duplicate slice_id detected: raise to prevent later files silently
            # overwriting earlier discovered slices.
            existing = slice_map[sid_str]
            raise SelectorError(f"duplicate slice_id '{sid_str}' found in {existing!s} and {p!s}")
        slice_map[sid_str] = p
        fm_map[sid_str] = fm

    # Start with all discovered slice ids
    matched_ids = set(slice_map.keys())

    # Project facet
    if project:
        proj_matched = {sid for sid, fm in fm_map.items() if str(fm.get("project")) == str(project)}
        matched_ids &= proj_matched

    # Tags facet: any-match
    if tags:
        tags_set = set(tags)
        tags_matched = set()
        for sid, fm in fm_map.items():
            t = fm.get("tags")
            if isinstance(t, list):
                if tags_set & set(map(str, t)):
                    tags_matched.add(sid)
        matched_ids &= tags_matched

    # Entity facet
    if entity:
        entity_ids = set(_entity_slice_ids(memory_root, entity))
        matched_ids &= entity_ids

    # Apply active-set filter unless include_decayed True
    ids_list = list(matched_ids)
    if not ids_list:
        return []

    if not include_decayed:
        active = retrieval_set.active_records(memory_root, ids_list)
        ids_list = [i for i in ids_list if i in active]

    # Build paths preserving slice_map ordering
    result: List[Path] = []
    for sid, path in slice_map.items():
        if sid in ids_list:
            result.append(path)
    return result
