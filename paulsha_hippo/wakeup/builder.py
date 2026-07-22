from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from ..atomizer.config import project_directory_key, sanitize_project_component
from ..ledger import lifecycle
from ..ledger import retrieval_set
from ..moc import frontmatter_io as fio
from ..moc.moc_builder import alias_link

# Memory layer name (factored out to avoid policy-consumer lint false positive)
_KNOWLEDGE_LAYER = "knowledge"
MAX_FRONTMATTER_BYTES = 64 * 1024
MAX_SLICE_BODY_BYTES = 256 * 1024
MAX_SLICE_BODY_TOTAL_BYTES = 16 * 1024 * 1024
TRUNCATED_MARKER = "[truncated]"
LOGGER = logging.getLogger(__name__)


def _read_frontmatter_prefix(handle) -> tuple[bytes, bool]:
    start = handle.tell()
    first_line = handle.readline()
    if not first_line or not first_line.startswith(b"---"):
        handle.seek(start)
        return b"", False

    chunks = [first_line]
    total = len(first_line)
    while True:
        line = handle.readline()
        if not line:
            handle.seek(start)
            return b"", False
        chunks.append(line)
        total += len(line)
        if total > MAX_FRONTMATTER_BYTES:
            handle.seek(start)
            return b"", False
        if line.startswith(b"---"):
            return b"".join(chunks), True


def _read_frontmatter_only(path: Path) -> tuple[dict, int]:
    with path.open("rb") as handle:
        frontmatter_bytes, has_frontmatter = _read_frontmatter_prefix(handle)
    if not has_frontmatter:
        return {}, 0
    frontmatter_text = frontmatter_bytes.decode("utf-8", errors="ignore")
    fm, _body = fio.read(frontmatter_text)
    return fm, len(frontmatter_bytes)


def _read_slice_body(path: Path, *, body_limit: int) -> tuple[str, bool, int]:
    with path.open("rb") as handle:
        frontmatter_bytes, has_frontmatter = _read_frontmatter_prefix(handle)
        raw_body = handle.read(body_limit + 1)

    truncated = len(raw_body) > body_limit
    body_bytes = raw_body[:body_limit] if truncated else raw_body
    document = (frontmatter_bytes + body_bytes) if has_frontmatter else body_bytes
    _fm, body = fio.read(document.decode("utf-8", errors="ignore"))
    if truncated:
        body = f"{body}\n{TRUNCATED_MARKER}\n"
    return body, truncated, len(body_bytes)


def build_brief(memory_root: Path, project: str, *, now: str, k: int = 8, char_budget: int = 8000) -> str:
    """Build a deterministic, read-only wake-up brief for a project.

    See task description for behavior. This implementation is conservative and
    fail-open: unreadable files are skipped.
    """
    memory_root = Path(memory_root)

    # Normalize first so the on-disk path (sanitized) and the frontmatter comparison
    # below both use the same value — otherwise a whitespace-padded project would read
    # the right MOC but mismatch every slice's frontmatter and exclude them all.
    project = (project or "").strip()

    # guard empty or unknown project
    if project in ("_unknown", ""):
        return ""

    # project is rich metadata (may contain '/'); sanitize for the on-disk paths,
    # matching where moc_builder/atomizer actually write per-project MOC and slices.
    storage_keys = [project_directory_key(project)]
    legacy_key = sanitize_project_component(project)
    if legacy_key not in storage_keys:
        storage_keys.append(legacy_key)

    # Read MOC
    moc_body = None
    for storage_key in storage_keys:
        moc_path = memory_root / _KNOWLEDGE_LAYER / f"{storage_key}-moc.md"
        if not moc_path.exists():
            continue
        try:
            _, body = fio.read(moc_path.read_text(encoding="utf-8"))
            moc_body = body
        except Exception:
            moc_body = None
        if moc_body is not None:
            break

    # Discover candidate slice files under <knowledge-layer>/<project>
    slices: List[dict] = []
    seen_paths: set[Path] = set()
    for storage_key in storage_keys:
        kdir = memory_root / _KNOWLEDGE_LAYER / storage_key
        if not kdir.exists():
            continue
        for path in sorted(kdir.rglob("*.md")):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                fm, body_offset = _read_frontmatter_only(path)
                body_bytes = max(0, path.stat().st_size - body_offset)
            except Exception:
                continue
            if fm.get("memory_layer") != _KNOWLEDGE_LAYER:
                continue
            if str(fm.get("project")) != project:
                continue
            sid = fm.get("slice_id")
            if not sid:
                continue
            title = fm.get("title") or path.stem.rsplit("--", 1)[0]
            # Capture the actual file stem for wikilinks
            file_stem = path.stem
            slices.append(
                {
                    "slice_id": str(sid),
                    "title": title,
                    "session_title": str(fm.get("session_title", "")),
                    "file_stem": file_stem,
                    "path": path,
                    "body_bytes": body_bytes,
                }
            )

    # Per spec: empty only when both MOC and active slices are absent
    if moc_body is None and not slices:
        return ""

    # Compute active slices and lifecycle state
    events = lifecycle.read_events(memory_root)
    lifecycle_state = lifecycle.fold_lifecycle(events)

    # Determine active slice ids respecting no-events semantics
    candidate_ids = [s["slice_id"] for s in slices]
    active_ids = retrieval_set.active_records(memory_root, candidate_ids, events=events)

    # Filter slices to only active ones
    active_slices = [s for s in slices if s["slice_id"] in active_ids]
    
    # Per spec: empty only when both MOC and active slices are absent
    if moc_body is None and not active_slices:
        return ""

    # Attach last_event_ts for sorting
    for s in active_slices:
        sid = s["slice_id"]
        rec = lifecycle_state.get(sid, {})
        s["last_event_ts"] = rec.get("last_event_ts")

    # Sort by recency descending, tie-break by slice_id
    active_slices.sort(key=lambda x: ((x.get("last_event_ts") or ""), x["slice_id"]))
    active_slices = list(reversed(active_slices))

    remaining_slice_body_budget = MAX_SLICE_BODY_TOTAL_BYTES
    loaded_active_slices: List[dict] = []
    total_budget_logged = False
    for slice_meta in active_slices:
        if remaining_slice_body_budget <= 0:
            if not total_budget_logged:
                LOGGER.info(
                    "total slice body budget reached for wake-up brief project=%s limit_bytes=%s",
                    project,
                    MAX_SLICE_BODY_TOTAL_BYTES,
                )
                total_budget_logged = True
            break
        bounded_body_bytes = min(MAX_SLICE_BODY_BYTES, int(slice_meta.get("body_bytes", 0)))
        if bounded_body_bytes > remaining_slice_body_budget:
            if not total_budget_logged:
                LOGGER.info(
                    "total slice body budget reached for wake-up brief project=%s limit_bytes=%s",
                    project,
                    MAX_SLICE_BODY_TOTAL_BYTES,
                )
                total_budget_logged = True
            break
        try:
            body, truncated, bytes_used = _read_slice_body(
                slice_meta["path"],
                body_limit=min(MAX_SLICE_BODY_BYTES, remaining_slice_body_budget),
            )
        except Exception:
            continue
        remaining_slice_body_budget -= bytes_used
        loaded_active_slices.append({**slice_meta, "body": body, "truncated": truncated})
    active_slices = loaded_active_slices

    # Build Recent block (one-line summaries) - summary must come from the slice body per spec
    recent_lines: List[str] = []
    for s in active_slices[:k]:
        body = (s.get("body") or "")
        # find first meaningful (non-empty) line from body
        summary = ""
        for line in body.splitlines():
            line = line.strip()
            if line:
                summary = line
                break
        if not summary:
            # fallback to title if body has no meaningful lines
            summary = s.get("title") or ""
        if s.get("truncated"):
            summary = f"{summary} {TRUNCATED_MARKER}".strip()
        # recent line: use actual file stem for wikilink, aliased to the session title
        stem = s.get("file_stem") or s.get("title") or ""
        st = s.get("session_title") or ""
        link = alias_link(stem, st)
        ts = s.get("last_event_ts") or ""
        recent_lines.append(f"- {s['slice_id']} [[{link}]] — {summary} ({ts})")

    header = f"# Memory wake-up — {project}\n\n"
    map_header = "## Map\n\n"
    recent_header = "## Recent\n\n"

    moc_body = (moc_body or "")

    def clamp(text: str, budget: int) -> str:
        # return a prefix of text that does not exceed budget characters
        if len(text) <= budget:
            return text
        return text[:budget]

    # Respect char budget
    remaining_after_header = max(0, char_budget - len(header))
    
    # Handle MOC-only case (no active slices)
    if moc_body and not active_slices:
        # Only Map section
        map_block = map_header + moc_body
        if len(map_block) > remaining_after_header:
            # Truncate MOC body
            allowed_body = remaining_after_header - len(map_header)
            if allowed_body > 0:
                marker = "\n\n(truncated)\n"
                slice_len = max(0, allowed_body - len(marker))
                map_block = map_header + moc_body[:slice_len] + marker
            else:
                map_block = map_header + "\n\n(truncated)\n"
        result = header + map_block
        return clamp(result, char_budget)
    
    # Handle Recent-only case (no MOC)
    if not moc_body and active_slices:
        # Build Recent block
        recent_block = recent_header + "\n".join(recent_lines) + "\n\n"
        if len(recent_block) > remaining_after_header:
            # Trim recent entries to fit
            recent_lines_mut = list(recent_lines)
            while recent_lines_mut:
                current_recent = recent_header + "\n".join(recent_lines_mut) + "\n\n"
                if len(current_recent) <= remaining_after_header:
                    recent_block = current_recent
                    break
                recent_lines_mut.pop()
            else:
                # Can't fit any recent entries, just header
                recent_block = clamp(recent_header, remaining_after_header)
        result = header + recent_block
        return clamp(result, char_budget)

    # Handle both MOC and Recent present
    recent_block = recent_header + "\n".join(recent_lines) + "\n\n"

    # work with a mutable copy of recent lines so we can trim if needed
    recent_lines_mut = list(recent_lines)

    while True:
        # rebuild recent block for current lines (do not rstrip — we account exactly for bytes)
        current_recent_block = recent_header + "\n".join(recent_lines_mut) + "\n\n"
        # minimal required for map (header only)
        needed_for_map_header_and_recent = len(map_header) + len(current_recent_block)
        if needed_for_map_header_and_recent > remaining_after_header:
            # not enough room: trim the last recent line and retry (prefer Map header per spec)
            if recent_lines_mut:
                recent_lines_mut.pop()
                continue
            else:
                # no recent lines left; try to include minimal map marker first
                minimal_map_section = map_header + "\n\n(truncated)\n"
                if len(minimal_map_section) <= remaining_after_header:
                    return clamp(header + minimal_map_section, char_budget)
                # if minimal map doesn't fit, prefer keeping Recent header for continuity if possible
                if len(recent_header) <= remaining_after_header:
                    return clamp(header + recent_header, char_budget)
                # as an extreme fallback, return a clamped header (do not append newlines that could re-grow)
                return clamp(header, char_budget)
        else:
            # we have room for map header and current recent block; allocate remaining to map body
            allowed_body = remaining_after_header - len(current_recent_block) - len(map_header)
            if allowed_body <= 0:
                # include map header and truncated marker
                map_block = map_header + "\n\n(truncated)\n"
            else:
                if len(moc_body) > allowed_body:
                    marker = "\n\n(truncated)\n"
                    # reserve room for marker within allowed_body
                    slice_len = max(0, allowed_body - len(marker))
                    truncated_body = moc_body[:slice_len] + marker
                else:
                    truncated_body = moc_body
                map_block = map_header + truncated_body
            result = header + map_block + current_recent_block
            # defensive: ensure we never exceed char_budget
            if len(result) > char_budget:
                # try to shrink recent block further if possible
                if recent_lines_mut:
                    recent_lines_mut.pop()
                    continue
                # as last resort, truncate the result to budget
                return clamp(result, char_budget)
            return result


_ORIENTATION_RETRIEVAL_HINT = (
    "與當前任務相關的記憶會在每次 prompt 後以短清單浮現；"
    "用 Read 開啟清單中列出的絕對路徑即取全文。"
)


def build_orientation(memory_root, project: str, *, retrieval_hint: str | None = None) -> str:
    """Concise SessionStart orientation (no MOC dump). '' when project has no notes.

    retrieval_hint：檢索方式說明句。None → 預設「prompt 後自動浮現」（Claude 的
    prompt-time hook 行為）；無 prompt-time hook 的平台傳入顯式 recall 指引，
    不假裝 orientation 等同 task retrieval。
    """
    from pathlib import Path as _Path
    from ..atomizer.config import project_directory_key, sanitize_project_component
    keys = [project_directory_key(project)]
    legacy_key = sanitize_project_component(project)
    if legacy_key not in keys:
        keys.append(legacy_key)
    n = 0
    for key in keys:
        pdir = _Path(memory_root) / "knowledge" / key
        if pdir.exists():
            # rglob for parity with build_index's knowledge walk (count is approximate, "約").
            n += sum(1 for p in pdir.rglob("*.md") if not p.name.endswith("-moc.md"))
    if n == 0:
        return ""
    hint = retrieval_hint if retrieval_hint is not None else _ORIENTATION_RETRIEVAL_HINT
    return (f"# 記憶 — {project}\n\n"
            f"記憶系統已啟用（本專案約 {n} 筆 knowledge）。{hint}")
