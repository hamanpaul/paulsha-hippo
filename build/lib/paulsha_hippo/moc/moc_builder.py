# paulshaclaw/memory/moc/moc_builder.py
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..atomizer.config import sanitize_project_component
from ..ledger import retrieval_set
from . import frontmatter_io as fio


# Placeholder titles that should be treated as "no title" so a meaningful fallback
# (session_title → basename) surfaces instead (#146).
_NON_TITLES = {"untitled", "(無內容)", "(none)", "(unknown)"}


def _meaningful_title(title: str) -> str:
    """Return the title unless it is empty or a known placeholder non-title; then ''."""
    t = (title or "").strip()
    return "" if not t or t.lower() in _NON_TITLES else t


def alias_link(stem: str, title: str) -> str:
    """Render a wikilink target aliased to the session title when one is present.

    The title is free text, so neutralize the two chars that would deform the
    resulting ``[[stem|title]]``: ``|`` (splits target from alias) and ``]``
    (closes the link). Converted to fullwidth so the label stays readable.
    """
    safe = (title or "").replace("|", "｜").replace("]", "］").strip()
    return f"{stem}|{safe}" if safe else stem


def _active_slices(memory_root: Path) -> list[tuple[str, str, str, str, str, str, str]]:
    """Return (slice_id, project, basename, artifact_kind, session_key, session_title, atom_title)
    for active knowledge slices. session_key is the agent-qualified ``distilled_from``
    (``agent:session``) when present, else the bare ``source_session`` — grouping on it
    avoids merging same-id sessions captured by different agents under one spine."""
    knowledge = memory_root / "knowledge"
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    if not knowledge.exists():
        return rows
    candidates: list[tuple[str, str, str, str, str, str, str]] = []
    for path in sorted(knowledge.rglob("*.md")):
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            # Unreadable/non-UTF-8 slice: skip, never let one poison-pill abort the MOC build.
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if not sid:
            continue
        session_key = str(fm.get("distilled_from", "")) or str(fm.get("source_session", ""))
        candidates.append((str(sid), str(fm.get("project", "_unknown")), path.stem,
                           str(fm.get("artifact_kind", "")),
                           session_key, str(fm.get("session_title", "")),
                           str(fm.get("atom_title", ""))))
    active = set(retrieval_set.active_records(memory_root, [c[0] for c in candidates]))
    return [c for c in candidates if c[0] in active]


def _hierarchy_lines(
    rows: list[tuple[str, str, str, str, str, str, str]], *, show_project: bool = False
) -> list[str]:
    """Group active slices by (project, session_key) into a session-title spine with
    nested atoms. Row = (slice_id, project, basename, kind, session_key, session_title,
    atom_title); session_key is agent-qualified (see _active_slices) so same-id sessions
    from different agents do not collide. basename embeds the unique slice_id, so the sort
    is stable."""
    by_key: dict[tuple[str, str], list[tuple[str, str, str, str, str, str, str]]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for row in sorted(rows, key=lambda r: (r[1], r[4], r[2])):
        key = (row[1], row[4])
        if key not in by_key:
            order.append(key)
        by_key[key].append(row)
    lines: list[str] = []
    for key in order:
        group = by_key[key]
        session_title = _meaningful_title(group[0][5])
        if session_title:
            parent = session_title
        elif key[1]:           # has a session key but no title
            parent = group[0][2]
        else:                  # no session grouping at all
            parent = "(未分組)"
        lines.append(f"- {parent}")
        for sid, project, basename, kind, sk, st, at in group:
            label = _meaningful_title(at) or _meaningful_title(st) or basename
            suffix = f"{project} · {kind}" if show_project else kind
            lines.append(f"  - [[{alias_link(basename, label)}]] — {suffix}")
    return lines


def _write_moc(path: Path, kind: str, now: str, header: str, lines: list[str], project: str | None = None) -> None:
    fm = ["---", "memory_layer: moc", f"moc_kind: {kind}", f"generated_ts: {now}"]
    if project is not None:
        fm.append(f"project: {project}")
    fm.append("---")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm) + f"\n# {header}\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


def build_mocs(memory_root: Path, now: str) -> None:
    knowledge = memory_root / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    rows = _active_slices(memory_root)
    by_project: dict[str, list[tuple[str, str, str, str, str, str, str]]] = defaultdict(list)
    for row in rows:
        by_project[row[1]].append(row)

    for project, items in by_project.items():
        if project == "common-sense":
            continue
        lines = _hierarchy_lines(items)
        _write_moc(knowledge / f"{sanitize_project_component(project)}-moc.md", "project", now, f"{project} MOC", lines, project)

    cs = _hierarchy_lines([r for r in rows if r[1] == "common-sense"])
    _write_moc(knowledge / "common-sense-moc.md", "common-sense", now, "Common-sense MOC", cs)

    active_lines = ["## Active", ""] + _hierarchy_lines(rows, show_project=True)
    _write_moc(knowledge / "wiki-moc.md", "wiki", now, "Wiki MOC", active_lines)
