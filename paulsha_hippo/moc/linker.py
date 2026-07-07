from __future__ import annotations

from pathlib import Path

from ..ledger import relations
from . import frontmatter_io as fio


def _slice_files(memory_root: Path) -> dict[str, Path]:
    """slice_id -> path, for memory_layer: knowledge files."""
    mapping: dict[str, Path] = {}
    knowledge = memory_root / "knowledge"
    if not knowledge.exists():
        return mapping
    for path in sorted(knowledge.rglob("*.md")):
        fm, _ = fio.read(path.read_text(encoding="utf-8"))
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if sid:
            mapping[str(sid)] = path
    return mapping


def materialize_links(memory_root: Path) -> dict[str, int]:
    files = _slice_files(memory_root)
    # build bidirectional adjacency
    related: dict[str, set[str]] = {sid: set() for sid in files}
    for edge in relations.read_edges(memory_root):
        etype = edge.get("type")
        frm = str(edge.get("from", ""))
        to = str(edge.get("to", ""))
        if etype == "relates_to" and frm.startswith("slice:") and to.startswith("slice:"):
            a, b = frm[len("slice:"):], to[len("slice:"):]
            if a in related:
                related[a].add(f"slice:{b}")
            if b in related:
                related[b].add(f"slice:{a}")
        elif etype == "mentions" and frm.startswith("slice:") and to.startswith("entity:"):
            a = frm[len("slice:"):]
            if a in related:
                related[a].add(to)  # entity:<NAME>

    weights: dict[str, int] = {}
    for sid, path in files.items():
        fm, body = fio.read(path.read_text(encoding="utf-8"))
        links: list[str] = []
        for node in sorted(related.get(sid, set())):
            if node.startswith("slice:"):
                target = files.get(node[len("slice:"):])
                if target is not None:
                    links.append(f"[[{target.stem}]]")
            elif node.startswith("entity:"):
                links.append(f"[[{node[len('entity:'):]}]]")
        title = fm.get("title") or path.stem.rsplit("--", 1)[0]
        fio.update(path, {"title": title, "aliases": [title], "related": links})
        weights[sid] = len(links)
    return weights
