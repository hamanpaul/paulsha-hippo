"""Force re-extract existing archived session payloads back into inbox (content + title).

Existing imports de-duplicate by checksum, so a plain re-run is skipped. Backfill
re-renders the inbox `.md` directly from each archived queue payload using the current
adapters + title generation, regenerating content for sessions captured before content
extraction existed. Dead transcript pointers yield empty content (skipped gracefully).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from paulsha_hippo import paths

from . import _git, title
from .classifier import classify_session
from .frontmatter import render_markdown
from .pipeline import _date_parts, _extract, safe_key
from .project_resolver import normalize_remote, resolve_project


def _reextract_one(payload_path: Path, root: Path, *, dry_run: bool) -> dict[str, Any]:
    result = _extract(payload_path)
    session = title.apply(dict(result.session), memory_root=root)
    remote = (
        result.raw_payload.get("remote_url")
        or result.raw_payload.get("remote")
        or session.get("repo")
    )
    captured_at, day, _ = _date_parts(session)
    bucket = classify_session(session)
    project = resolve_project(
        cwd=session.get("cwd"),
        git_toplevel=session.get("repo"),
        remote_url=remote if isinstance(remote, str) else None,
        memory_root=str(root),
    )
    inbox_path = root / "inbox" / bucket / session["tool"] / day / f"{safe_key(session['session_id'])}.md"
    session["raw_payload_pointer"] = str(payload_path)
    provenance_repo = normalize_remote(_git.git_remote(_git.git_toplevel(session.get("cwd")))) or "_unknown"
    rendered = render_markdown(
        session,
        project=project,
        classifier_bucket=bucket,
        captured_at=captured_at,
        provenance_repo=provenance_repo,
    )
    if not dry_run:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = inbox_path.with_name(f".{inbox_path.name}.tmp")
        tmp.write_text(rendered, encoding="utf-8")
        tmp.replace(inbox_path)
    return {"session": session["session_id"], "inbox_path": str(inbox_path)}


def run(memory_root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    root = Path(memory_root)
    queue = root / "archive" / "queue"
    items: list[dict[str, Any]] = []
    for payload in (sorted(queue.rglob("*.json")) if queue.is_dir() else []):
        try:
            items.append(_reextract_one(payload, root, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 - backfill boundary; keep going on bad payloads
            items.append({"payload": str(payload), "error": type(exc).__name__})
    return {"count": len(items), "dry_run": dry_run, "items": items}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill Stage 2 inbox content+title from archived queue payloads"
    )
    ap.add_argument("--memory-root", default=str(paths.memory_root()))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = run(args.memory_root, dry_run=args.dry_run)
    print(f"{'DRY-RUN ' if res['dry_run'] else ''}backfilled {res['count']} session(s)")


if __name__ == "__main__":
    main()
