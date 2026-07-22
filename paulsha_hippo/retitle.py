"""One-shot migration: regenerate titles for `untitled` knowledge slices (#151).

Slices whose title-generation failed (external agent offline at import time) land as
`title: untitled` with a `untitled--<slice_id>.md` filename — visible in the raw
wake-up brief injected to agents. This migration distils a real title from the
slice body, stamps `title`/`atom_title`/`aliases`, and renames the file to
`<slug>--<slice_id>.md` (slice_id and body preserved; MOC/relations index by
slice_id so the rename is safe). doc-fragment candidates are skipped (left for
`prune-noise`); slices the distiller can't title (LLM offline) are skipped too,
so the migration never invents a junk title and never fails as a whole.
Since #178 the scan also covers generic titles (``noise.is_generic_title``:
report-*/task-*/todo-*/overview/problem/review-summary) so pool-excluded
generic artifacts can regain a specific title and re-enter the retrieval pool.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable

from .moc import frontmatter_io as _fio
from .moc import naming as _naming
from .moc.runner import run_moc
from .noise import DocCorpus, classify_noise, is_generic_title

Distiller = Callable[[str], "str | None"]


def _is_untitled(frontmatter: dict, path: Path) -> bool:
    title = str(frontmatter.get("title", "")).strip()
    return title == "untitled" or path.name.startswith("untitled--") or is_generic_title(title)


def _write_manifest(manifest: Path, rows: list[dict]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = manifest.with_name(f".{manifest.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(manifest)


def retitle_untitled(
    memory_root: Path,
    *,
    now: str,
    apply: bool,
    distill: Distiller,
    doc_corpus: DocCorpus | None = None,
    projects: "list[str] | None" = None,
) -> dict:
    """Retitle untitled real-knowledge slices. Returns a summary dict and always
    writes an audit manifest to ``runtime/ledger/retitle-<now>.jsonl``.

    ``projects`` (optional) restricts the scan to the named projects."""
    knowledge = memory_root / "knowledge"
    rows: list[dict] = []

    for path in sorted(knowledge.rglob("*.md")) if knowledge.exists() else []:
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = _fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        if projects and str(fm.get("project", "")) not in projects:
            continue
        if not _is_untitled(fm, path):
            continue

        slice_id = str(fm.get("slice_id", ""))
        base = {"slice_id": slice_id, "project": str(fm.get("project", "")),
                "path": str(path), "old_name": path.name}

        # Guard: doc-fragments belong to prune-noise, never retitle them.
        if classify_noise(fm, body, doc_corpus=doc_corpus).is_noise:
            rows.append({**base, "status": "skipped", "reason": "noise"})
            continue

        title = distill(body)
        if not title or not title.strip():
            rows.append({**base, "status": "skipped", "reason": "distill-failed"})
            continue
        title = title.strip()
        new_name = _naming.slice_filename(title, slice_id)

        if not apply:
            rows.append({**base, "new_name": new_name, "title": title, "status": "dry-run"})
            continue

        _fio.update(path, {"title": title, "atom_title": title, "aliases": [title]})
        target = path.with_name(new_name)
        # Rename only when the target slot is free. A taken slot means a duplicate
        # slice_id file already holds the name; leave the file stamped-but-unrenamed
        # and record it honestly ("stamped") so the manifest stays a trustworthy gate
        # — run_moc/reconcile resolves the duplicate by mtime on the rebuild below.
        if target == path or not target.exists():
            if target != path:
                path.rename(target)
            status = "retitled"
        else:
            status = "stamped"
        rows.append({**base, "new_name": new_name, "title": title, "status": status})

    manifest = memory_root / "runtime" / "ledger" / f"retitle-{now.replace(':', '')}.jsonl"
    _write_manifest(manifest, rows)

    counts = Counter(r["status"] for r in rows)
    if apply and (counts.get("retitled", 0) or counts.get("stamped", 0)):
        run_moc(memory_root, now)

    return {
        "candidates": len(rows),
        "applied": apply,
        "retitled": counts.get("retitled", 0),
        "stamped": counts.get("stamped", 0),
        "planned": counts.get("dry-run", 0),
        "skipped": counts.get("skipped", 0),
        "manifest": str(manifest),
    }
