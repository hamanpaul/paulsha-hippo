"""Ownership-manifest driven, recoverable install transaction.

This module is deliberately filesystem-only.  It does not restart services,
touch shell startup files, mutate memory/ledger/index/recovery/project-registry
state, or inspect credential stores.  A caller may use the resulting manifest
to perform those live gates outside the isolated worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class DeploymentError(ValueError):
    """Unsafe manifest or an inconsistent transaction."""


PROTECTED_PREFIXES = (
    ".bashrc",
    ".zshrc",
    ".profile",
    ".config/paulshaclaw",
    ".config/claude",
    ".config/codex",
    ".config/copilot",
    ".config/systemd",
    ".config/environment.d",
    ".config/github-copilot",
    ".config/gcloud",
    ".config/gh",
    ".config/anthropic",
    ".config/openai",
    ".local/bin",
    "bin",
    ".claude",
    ".codex",
    ".copilot",
    ".agents/memory/inbox",
    ".agents/memory/archive",
    ".agents/memory/knowledge",
    ".agents/memory/runtime/ledger",
    ".agents/memory/runtime/" + "indexes",
    ".agents/memory/runtime/recovery",
    ".agents/memory/runtime/logs",
    ".agents/config/projects.yaml",
    ".agents/config/paulsha/project-hippo.yaml",
    "inbox",
    "archive",
    "knowledge",
    "runtime/ledger",
    "runtime/" + "indexes",
    "runtime/recovery",
    "runtime/logs",
    "config/projects.yaml",
    "config/project-hippo.yaml",
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_relative(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DeploymentError("manifest path must be relative and traversal-free")
    if not candidate.parts or any(part in {"", "."} for part in candidate.parts):
        raise DeploymentError("manifest path is empty")
    return candidate


def _is_protected(relative: Path) -> bool:
    # Keep a leading dot: `.config` and `.local` are protected roots too.
    text = relative.as_posix().lstrip("/")
    return any(text == prefix or text.startswith(prefix + "/") for prefix in PROTECTED_PREFIXES)


def _target_path(target_root: Path, relative: Path) -> Path:
    root = target_root.resolve()
    target = (root / relative).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DeploymentError("manifest target escapes target root") from exc
    return target


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"invalid manifest: {path.name}") from exc
    if not isinstance(value, dict):
        raise DeploymentError("manifest root must be an object")
    return value


def _entry_bytes(entry: Mapping[str, Any], package_root: Path) -> bytes:
    if "content" in entry:
        value = entry["content"]
        if not isinstance(value, str):
            raise DeploymentError("manifest content must be text")
        return value.encode("utf-8")
    source = entry.get("source")
    if not isinstance(source, str) or not source:
        raise DeploymentError("manifest entry needs content or source")
    source_path = (package_root / _safe_relative(source)).resolve()
    try:
        source_path.relative_to(package_root.resolve())
    except ValueError as exc:
        raise DeploymentError("manifest source escapes package root") from exc
    try:
        return source_path.read_bytes()
    except OSError as exc:
        raise DeploymentError("manifest source is unavailable") from exc


def _entry_path(entry: Mapping[str, Any], target_root: Path) -> tuple[Path, Path]:
    raw = entry.get("path")
    if not isinstance(raw, str):
        raise DeploymentError("manifest entry path must be a string")
    relative = _safe_relative(raw)
    if _is_protected(relative):
        raise DeploymentError(f"protected path is not install-owned: {relative.as_posix()}")
    return relative, _target_path(target_root, relative)


def _load_entries(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise DeploymentError("manifest entries must be a list")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise DeploymentError("manifest entry must be an object")
        path = raw.get("path")
        if not isinstance(path, str) or path in seen:
            raise DeploymentError("manifest entries must have unique paths")
        seen.add(path)
        result.append(raw)
    return result


def _shared_desired(entry: Mapping[str, Any]) -> dict[str, Any]:
    value = entry.get("owned_entries", {})
    if not isinstance(value, Mapping):
        raise DeploymentError("shared owned_entries must be an object")
    return {str(key): value[key] for key in value}


def _read_shared(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("shared target is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DeploymentError("shared target must contain a JSON object")
    return value


def _entry_plan(entry: Mapping[str, Any], target_root: Path, package_root: Path, previous: Mapping[str, Any]) -> dict[str, Any]:
    relative, path = _entry_path(entry, target_root)
    kind = str(entry.get("kind", "exclusive"))
    if kind not in {"exclusive", "shared-json"}:
        raise DeploymentError(f"unsupported ownership kind: {kind}")
    if kind == "shared-json":
        desired_owned = _shared_desired(entry)
        current = _read_shared(path)
        previous_owned = previous.get("owned_entries", {}) if isinstance(previous, Mapping) else {}
        if not isinstance(previous_owned, Mapping):
            previous_owned = {}
        effective = dict(current)
        conflicts: list[str] = []
        for key, value in desired_owned.items():
            old = previous_owned.get(key, object())
            if key in current and key in previous_owned and current[key] != old and current[key] != value:
                conflicts.append(key)
            effective[key] = value
        return {
            "path": relative.as_posix(),
            "kind": kind,
            "action": "keep" if effective == current else "update",
            "current_hash": _sha_bytes(_json_bytes(current)) if path.exists() else None,
            "desired_hash": _sha_bytes(_json_bytes(effective)),
            "owned_entries": desired_owned,
            "previous_owned_entries": dict(previous_owned),
            "conflicts": conflicts,
        }
    desired = _entry_bytes(entry, package_root)
    current = path.read_bytes() if path.is_file() else None
    previous_hash = previous.get("sha256") if isinstance(previous, Mapping) else None
    current_hash = _sha_bytes(current) if current is not None else None
    if current is None:
        action = "create"
    elif current == desired:
        action = "keep"
    elif previous_hash and current_hash == previous_hash:
        action = "update"
    else:
        action = "conflict"
    return {
        "path": relative.as_posix(),
        "kind": kind,
        "action": action,
        "current_hash": current_hash,
        "desired_hash": _sha_bytes(desired),
        "previous_hash": previous_hash,
    }


def plan_install(
    manifest_path: str | Path,
    *,
    target_root: str | Path,
    package_root: str | Path | None = None,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser()
    manifest = _read_json(manifest_file)
    entries = _load_entries(manifest)
    target = Path(target_root).expanduser()
    package = Path(package_root).expanduser() if package_root else manifest_file.parent
    # State belongs to the target surface, not beside the package manifest.
    # The latter may be inside a read-only wheel/site-packages directory and
    # must never become an implicit write target.
    state_file = Path(state_path) if state_path else target / ".hippo-install-state.json"
    previous = _read_json(state_file) if state_file.exists() else {}
    previous_entries = previous.get("entries", {}) if isinstance(previous.get("entries", {}), Mapping) else {}
    rows = [_entry_plan(entry, target, package, previous_entries.get(str(entry.get("path")), {})) for entry in entries]
    desired_paths = {row["path"] for row in rows}
    for path_text, old in previous_entries.items():
        if path_text in desired_paths or not isinstance(old, Mapping):
            continue
        relative = _safe_relative(str(path_text))
        if _is_protected(relative):
            raise DeploymentError(f"protected stale path is not install-owned: {path_text}")
        target_path = _target_path(target, relative)
        if str(old.get("kind", "exclusive")) == "shared-json":
            current = _read_shared(target_path)
            owned = old.get("owned_entries", {}) if isinstance(old.get("owned_entries", {}), Mapping) else {}
            removable = {key: value for key, value in owned.items() if current.get(key, object()) == value}
            rows.append({
                "path": path_text,
                "kind": "shared-json",
                "action": "shared-remove" if removable else "keep",
                "current_hash": _sha_bytes(_json_bytes(current)) if target_path.exists() else None,
                "previous_hash": old.get("sha256"),
                "owned_entries": dict(owned),
                "remove_entries": removable,
                "previous_owned_entries": dict(owned),
                "conflicts": [],
            })
            continue
        current_hash = _sha_bytes(target_path.read_bytes()) if target_path.is_file() else None
        old_hash = old.get("sha256")
        rows.append({
            "path": path_text,
            "kind": str(old.get("kind", "exclusive")),
            "action": "remove" if current_hash in {old_hash, None} else "conflict",
            "current_hash": current_hash,
            "previous_hash": old_hash,
        })
    conflicts = [row["path"] for row in rows if row.get("action") == "conflict" or row.get("conflicts")]
    return {
        "schema_version": "1",
        "manifest": str(manifest_file),
        "target_root": str(target.resolve()),
        "state_path": str(state_file),
        "force_required": any(row["action"] in {"create", "update", "remove"} for row in rows),
        "conflicts": conflicts,
        "entries": rows,
    }


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".hippo-install-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        _fsync_parent(path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _backup(path: Path, backup_root: Path, relative: str) -> str | None:
    if not path.exists():
        return None
    destination = backup_root / _safe_relative(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return str(destination)


def apply_install(
    plan: Mapping[str, Any],
    *,
    manifest_path: str | Path,
    package_root: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    transaction_root: str | Path | None = None,
) -> dict[str, Any]:
    if plan.get("conflicts"):
        raise DeploymentError("ownership conflict requires operator review")
    if not force and plan.get("force_required"):
        raise DeploymentError("--force is required for ownership changes")
    if dry_run:
        return {"status": "dry-run", "plan": dict(plan)}
    target = Path(str(plan["target_root"]))
    manifest_file = Path(manifest_path)
    manifest = _read_json(manifest_file)
    entries = {str(entry["path"]): entry for entry in _load_entries(manifest)}
    package = Path(package_root).expanduser() if package_root else manifest_file.parent
    token = secrets.token_hex(8)
    tx_root = Path(transaction_root) if transaction_root else target / ".hippo-install-backups" / token
    tx_root.mkdir(parents=True, exist_ok=True)
    journal = tx_root / "transaction.json"
    state_path = Path(str(plan["state_path"]))
    state_existed = state_path.is_file()
    state_backup = tx_root / "state-before.json"
    if state_existed:
        shutil.copy2(state_path, state_backup)
    journal_payload = {
        "schema_version": "1",
        "state": "prepared",
        "plan": dict(plan),
        "token": token,
        "state_path": str(state_path),
        "state_existed": state_existed,
        "state_backup": str(state_backup) if state_existed else None,
    }
    _atomic_write(journal, _json_bytes(journal_payload))
    applied: list[dict[str, Any]] = []
    try:
        for row in plan.get("entries", []):
            path = _target_path(target, _safe_relative(str(row["path"])))
            kind = row.get("kind", "exclusive")
            # Shared files are never copied wholesale.  Their inverse patch is
            # recorded below, preserving concurrent user-owned keys.
            backup = None if kind == "shared-json" else _backup(path, tx_root / "preimage", str(row["path"]))
            entry = entries.get(str(row["path"]))
            shared_before: dict[str, Any] | None = None
            shared_after: dict[str, Any] | None = None
            shared_file_before = path.is_file()
            if row["action"] in {"create", "update", "shared-remove"} and (entry is not None or kind == "shared-json"):
                if kind == "shared-json":
                    current = _read_shared(path)
                    owned_values = _shared_desired(entry) if entry is not None else dict(row.get("remove_entries", {}))
                    shared_before = {
                        key: {"present": key in current, "value": current.get(key)}
                        for key in owned_values
                    }
                    if entry is not None:
                        for key, value in owned_values.items():
                            current[key] = value
                    else:
                        for key, value in owned_values.items():
                            if current.get(key, object()) == value:
                                current.pop(key, None)
                    shared_after = {
                        key: {"present": key in current, "value": current.get(key)}
                        for key in owned_values
                    }
                    if current:
                        _atomic_write(path, _json_bytes(current))
                    elif path.exists():
                        path.unlink()
                        _fsync_parent(path)
                else:
                    _atomic_write(path, _entry_bytes(entry, package))
            elif row["action"] == "remove" and path.exists():
                path.unlink()
                _fsync_parent(path)
            applied.append({
                "path": str(row["path"]),
                "kind": kind,
                "action": row["action"],
                "backup": backup,
                "shared_before": shared_before,
                "shared_after": shared_after,
                "shared_file_before": shared_file_before,
            })
        state_entries: dict[str, Any] = {}
        for entry in _load_entries(manifest):
            row = next(item for item in plan["entries"] if item["path"] == entry["path"])
            if entry.get("kind", "exclusive") == "shared-json":
                state_entries[str(entry["path"])] = {
                    "kind": "shared-json",
                    "owned_entries": _shared_desired(entry),
                    "sha256": row.get("desired_hash"),
                }
            else:
                state_entries[str(entry["path"])] = {
                    "kind": "exclusive",
                    "sha256": row.get("desired_hash"),
                }
        _atomic_write(state_path, _json_bytes({"schema_version": "1", "entries": state_entries}))
        _atomic_write(journal, _json_bytes({**journal_payload, "state": "committed", "applied": applied}))
    except Exception as exc:
        _atomic_write(journal, _json_bytes({**journal_payload, "state": "prepared", "applied": applied}))
        rollback_install({"journal": str(journal), "target_root": str(target)})
        raise DeploymentError("install transaction rolled back") from exc
    return {"status": "applied", "transaction": str(journal), "applied": applied}


def rollback_install(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    journal_path = Path(value["journal"] if isinstance(value, Mapping) else value)
    journal = _read_json(journal_path)
    target = Path(str(journal.get("plan", {}).get("target_root") or value.get("target_root", ""))) if isinstance(value, Mapping) else Path(str(journal.get("plan", {}).get("target_root", "")))
    preimage = journal_path.parent / "preimage"
    shared_conflicts: list[str] = []
    # Preflight the shared inverse patches.  A user edit made after apply must
    # block rollback instead of being overwritten by an old owned value.
    for row in journal.get("applied", []):
        before = row.get("shared_before")
        if row.get("kind") != "shared-json" or not isinstance(before, Mapping):
            continue
        destination = _target_path(target, _safe_relative(str(row["path"])))
        current = _read_shared(destination)
        after = row.get("shared_after") if isinstance(row.get("shared_after"), Mapping) else {}
        for key, expected in after.items():
            actual = {"present": key in current, "value": current.get(key)}
            if actual != expected:
                shared_conflicts.append(f"{row['path']}:{key}")
    if shared_conflicts:
        _atomic_write(
            journal_path,
            _json_bytes({**journal, "state": "rollback-blocked", "conflicts": sorted(shared_conflicts)}),
        )
        return {
            "status": "rollback-blocked",
            "journal": str(journal_path),
            "conflicts": sorted(shared_conflicts),
        }
    restored: list[str] = []
    for path in sorted(preimage.rglob("*")) if preimage.exists() else []:
        if not path.is_file():
            continue
        relative = path.relative_to(preimage)
        destination = _target_path(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        restored.append(relative.as_posix())
    for row in journal.get("applied", []):
        if row.get("kind") == "shared-json" and row.get("shared_before") is not None:
            destination = _target_path(target, _safe_relative(str(row["path"])))
            current = _read_shared(destination)
            for key, before in row["shared_before"].items():
                if before.get("present"):
                    current[key] = before.get("value")
                else:
                    current.pop(key, None)
            if current:
                _atomic_write(destination, _json_bytes(current))
            elif row.get("shared_file_before"):
                _atomic_write(destination, _json_bytes(current))
            elif destination.exists():
                # Do not delete a user-created empty shared file during
                # rollback; only the owned keys are ours to compensate.
                pass
            continue
        if row.get("action") == "create" and not row.get("backup"):
            destination = _target_path(target, _safe_relative(str(row["path"])))
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
    state_path = journal.get("state_path")
    state_backup = journal.get("state_backup")
    if state_path:
        state_destination = Path(str(state_path))
        if state_backup and Path(str(state_backup)).is_file():
            _atomic_write(state_destination, Path(str(state_backup)).read_bytes())
        elif not journal.get("state_existed", False):
            try:
                state_destination.unlink()
            except FileNotFoundError:
                pass
    _atomic_write(journal_path, _json_bytes({**journal, "state": "rolled-back", "restored": restored}))
    return {"status": "rolled-back", "journal": str(journal_path), "restored": restored}


def install_all(
    *,
    manifest_path: str | Path,
    target_root: str | Path,
    package_root: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    transaction_root: str | Path | None = None,
) -> dict[str, Any]:
    plan = plan_install(
        manifest_path,
        target_root=target_root,
        package_root=package_root,
        state_path=Path(target_root).expanduser().resolve() / ".hippo-install-state.json",
    )
    return apply_install(
        plan,
        manifest_path=manifest_path,
        package_root=package_root,
        force=force,
        dry_run=dry_run,
        transaction_root=transaction_root,
    )


__all__ = [
    "DeploymentError",
    "PROTECTED_PREFIXES",
    "apply_install",
    "install_all",
    "plan_install",
    "rollback_install",
]
