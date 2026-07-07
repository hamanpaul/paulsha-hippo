"""One-shot migration: re-key knowledge slices from a legacy raw-remote project
key to a registered short slug (#177).

Recall filters by strict project equality, and some call sites also derive the
per-project directory from a sanitized project slug. A safe migration therefore
must rewrite frontmatter ``project`` AND move the file into the sanitized slug directory;
doing only one would leave recall in a torn state. The migration writes an
audit manifest, supports dry-run/apply, rebuilds MOCs and the retrieval index
through ``run_moc``, and never touches ``retrieval.db`` directly.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from tempfile import mkdtemp

from .atomizer.config import is_safe_path_component, sanitize_project_component
from .moc import frontmatter_io as _fio
from .moc.runner import run_moc


class RekeyError(ValueError):
    """Raised when the requested rekey operation is invalid."""


def _write_manifest(manifest: Path, rows: list[dict]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    tmp = manifest.with_name(f".{manifest.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(manifest)


def _slice_ids_in_dir(root: Path) -> set[str]:
    slice_ids: set[str] = set()
    if not root.exists():
        return slice_ids
    for path in sorted(root.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            frontmatter, _body = _fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if frontmatter.get("memory_layer") != "knowledge":
            continue
        slice_id = str(frontmatter.get("slice_id", ""))
        if slice_id:
            slice_ids.add(slice_id)
    return slice_ids


def _run_moc_preserving_conflicts(memory_root: Path, now: str, protected_paths: set[Path]) -> dict:
    existing = tuple(path for path in sorted(protected_paths) if path.exists())
    if not existing:
        return run_moc(memory_root, now)
    runtime = memory_root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    stash_root = Path(mkdtemp(dir=runtime, prefix="rekey-conflicts-"))
    moved: list[tuple[Path, Path]] = []
    result: dict | None = None
    pending_error: Exception | None = None
    try:
        for index, path in enumerate(existing):
            parked = stash_root / f"{index}-{path.name}"
            path.rename(parked)
            moved.append((path, parked))
        result = run_moc(memory_root, now)
    except Exception as exc:
        pending_error = exc
    finally:
        restore_errors: list[str] = []
        for original, parked in reversed(moved):
            if not parked.exists():
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                parked.rename(original)
            except OSError as exc:
                restore_errors.append(f"{original}: {exc}")
        if not restore_errors:
            try:
                stash_root.rmdir()
            except OSError:
                pass
        if restore_errors:
            pending_error = OSError(
                "restore blocked; parked conflicts preserved at "
                f"{stash_root}: {'; '.join(restore_errors)}"
            )
    if pending_error is not None:
        raise pending_error
    if result is None:
        raise OSError(f"rekey rebuild did not complete; parked conflicts preserved at {stash_root}")
    return result


def rekey_project(
    memory_root: Path,
    *,
    old_key: str,
    new_slug: str,
    now: str,
    apply: bool,
) -> dict:
    """Re-key knowledge slices whose frontmatter project equals ``old_key``."""

    if not is_safe_path_component(new_slug):
        raise RekeyError(f"--to must be a path-safe slug (no '/'): {new_slug!r}")
    if not old_key.strip() or old_key == new_slug:
        raise RekeyError("--from must be a non-empty key different from --to")

    knowledge = memory_root / "knowledge"
    target_dir = knowledge / sanitize_project_component(new_slug)
    target_slice_ids = _slice_ids_in_dir(target_dir)
    rows: list[dict] = []
    planned: list[tuple[Path, Path, dict]] = []
    planned_targets: set[Path] = set()

    for path in sorted(knowledge.rglob("*.md")) if knowledge.exists() else []:
        if path.name.endswith("-moc.md"):
            continue
        try:
            frontmatter, _body = _fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if frontmatter.get("memory_layer") != "knowledge":
            continue
        if str(frontmatter.get("project", "")) != old_key:
            continue

        target = target_dir / path.name
        base = {
            "slice_id": str(frontmatter.get("slice_id", "")),
            "from": old_key,
            "to": new_slug,
            "path": str(path),
            "target": str(target),
        }
        if not apply:
            rows.append({**base, "status": "dry-run"})
            continue
        slice_id = base["slice_id"]
        if target != path and (target.exists() or target in planned_targets or slice_id in target_slice_ids):
            rows.append({**base, "status": "conflict"})
            continue
        row = {**base, "status": "planned"}
        rows.append(row)
        planned.append((path, target, row))
        planned_targets.add(target)
        if slice_id:
            target_slice_ids.add(slice_id)

    manifest = memory_root / "runtime" / "ledger" / f"rekey-{now.replace(':', '')}.jsonl"
    _write_manifest(manifest, rows)

    for path, target, row in planned:
        moved = False
        active_path = path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target != path:
                path.rename(target)
                moved = True
                active_path = target
            _fio.update(active_path, {"project": new_slug})
            row["status"] = "rekeyed"
        except OSError as exc:
            if moved and active_path.exists() and not path.exists():
                try:
                    active_path.rename(path)
                    row["rollback_error"] = None
                except OSError as rollback_exc:
                    row["rollback_error"] = str(rollback_exc)
            row["status"] = "error"
            row["error"] = str(exc)

    if apply:
        _write_manifest(manifest, rows)

    counts = Counter(row["status"] for row in rows)
    removed_source_dir = False
    removed_orphan_moc = False
    indexed = None
    warnings: list[str] = []
    post_apply_errors = 0
    if apply and counts.get("rekeyed", 0):
        source_dir = knowledge / sanitize_project_component(old_key)
        if source_dir.is_dir() and not any(source_dir.iterdir()):
            try:
                source_dir.rmdir()
                removed_source_dir = True
            except OSError as exc:
                post_apply_errors += 1
                warnings.append(f"post-apply cleanup failed: {exc}")
        orphan_moc = knowledge / f"{sanitize_project_component(old_key)}-moc.md"
        if removed_source_dir and orphan_moc.exists():
            try:
                orphan_moc.unlink()
                removed_orphan_moc = True
            except OSError as exc:
                post_apply_errors += 1
                warnings.append(f"post-apply cleanup failed: {exc}")
        try:
            conflict_paths = {Path(row["path"]) for row in rows if row["status"] == "conflict"}
            moc_result = _run_moc_preserving_conflicts(memory_root, now, conflict_paths)
            indexed = moc_result.get("indexed")
            warnings.extend(list(moc_result.get("warnings", [])))
        except (OSError, UnicodeDecodeError) as exc:
            indexed = False
            post_apply_errors += 1
            warnings.append(f"post-apply rebuild failed: {exc}")

    return {
        "candidates": len(rows),
        "applied": apply,
        "rekeyed": counts.get("rekeyed", 0),
        "planned": counts.get("dry-run", 0),
        "conflicts": counts.get("conflict", 0),
        "errors": counts.get("error", 0) + post_apply_errors,
        "removed_source_dir": removed_source_dir,
        "removed_orphan_moc": removed_orphan_moc,
        "indexed": indexed,
        "warnings": warnings,
        "manifest": str(manifest),
    }
