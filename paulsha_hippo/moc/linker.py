from __future__ import annotations

from pathlib import Path

from ..ledger import relations
from . import frontmatter_io as fio


def _slice_files(memory_root: Path) -> tuple[dict[str, Path], list[str]]:
    """slice_id -> path, for memory_layer: knowledge files；附 per-file warnings。

    Fail-soft（#16）：讀不動的檔（權限、壞編碼）記 warning 跳過，
    不讓單一壞檔中止整輪 linker。
    """
    mapping: dict[str, Path] = {}
    warnings: list[str] = []
    knowledge = memory_root / "knowledge"
    if not knowledge.exists():
        return mapping, warnings
    for path in sorted(knowledge.rglob("*.md")):
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{path.name}: linker skipped ({exc})")
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if sid:
            mapping[str(sid)] = path
    return mapping, warnings


def materialize_links(memory_root: Path) -> tuple[dict[str, int], list[str]]:
    """物化 [[wikilink]] 到 frontmatter，回傳 (link weights, warnings)。

    Fail-soft（#16）：單一 slice 讀寫失敗只記 warning 跳過（該 slice 不進
    weights），其餘 slices 照常物化；relations ledger 本身壞掉仍向外拋
    （core-state corruption 由 runner 整體降級）。
    """
    files, warnings = _slice_files(memory_root)
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
        try:
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
        except Exception as exc:  # fail-soft: 單一壞 slice 不中止 linker
            warnings.append(f"{path.name}: linker skipped ({exc})")
            continue
        weights[sid] = len(links)
    return weights, warnings
