"""Staged artifact upgrade/rollback transaction.

The runner is intentionally independent from systemd, pipx, shell startup
files, credentials, and memory state.  It fences only the artifact target and
records the live service/profile verification as a required pending gate for
the caller to execute after installation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping


class UpgradeError(ValueError):
    """The artifact transaction is unsafe or drifted."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        try:
            parent_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("invalid upgrade manifest") from exc
    if not isinstance(value, dict):
        raise UpgradeError("upgrade manifest root must be an object")
    return value


def _artifact_name(value: str) -> str:
    path = Path(value)
    if path.name != value or value in {"", ".", ".."} or value.startswith("."):
        raise UpgradeError("artifact name must be a plain non-hidden filename")
    return value


def plan_upgrade(
    candidate: str | Path,
    *,
    target_root: str | Path,
    profile_id: str = "candidate",
    artifact_name: str = "hippo.whl",
) -> dict[str, Any]:
    source = Path(candidate).expanduser().resolve()
    if not source.is_file():
        raise UpgradeError("candidate artifact is not a regular file")
    name = _artifact_name(artifact_name)
    target_dir = Path(target_root).expanduser().resolve()
    target = target_dir / name
    current_hash = _sha(target) if target.is_file() else None
    return {
        "schema_version": "1",
        "candidate": str(source),
        "candidate_sha256": _sha(source),
        "target_root": str(target_dir),
        "target": str(target),
        "current_sha256": current_hash,
        "profile_id": str(profile_id),
        "state": "planned",
        "service_verification": "pending",
    }


def prepare_upgrade(
    plan: Mapping[str, Any] | str | Path,
    *,
    transaction_root: str | Path | None = None,
) -> dict[str, Any]:
    source = _load_plan(plan)
    candidate = Path(str(source.get("candidate", ""))).resolve()
    target = Path(str(source.get("target", ""))).resolve()
    if not candidate.is_file() or _sha(candidate) != source.get("candidate_sha256"):
        raise UpgradeError("candidate artifact drifted before prepare")
    if target.exists() and not target.is_file():
        raise UpgradeError("upgrade target is not a regular file")
    root = Path(transaction_root).expanduser().resolve() if transaction_root else target.parent / ".hippo-upgrade"
    root.mkdir(parents=True, exist_ok=True)
    candidate_copy = root / "candidate.whl"
    shutil.copy2(candidate, candidate_copy)
    if _sha(candidate_copy) != source["candidate_sha256"]:
        raise UpgradeError("candidate copy verification failed")
    previous = root / "previous.whl"
    if target.is_file():
        shutil.copy2(target, previous)
        if _sha(target) != source.get("current_sha256"):
            raise UpgradeError("upgrade target drifted before prepare")
    manifest = {
        **source,
        "state": "prepared",
        "transaction_root": str(root),
        "candidate_copy": str(candidate_copy),
        "previous_copy": str(previous) if previous.is_file() else None,
        "fence": str(root / "writer.fence"),
        "write_ahead": True,
    }
    manifest_path = root / "upgrade.json"
    _atomic_write(manifest_path, manifest)
    return {**manifest, "manifest": str(manifest_path)}


def _load_plan(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    result = dict(value) if isinstance(value, Mapping) else _read(value)
    required = ("candidate_sha256", "target", "target_root", "candidate")
    if any(not result.get(key) for key in required):
        raise UpgradeError("upgrade manifest is incomplete")
    target_root = Path(str(result["target_root"])).resolve()
    target = Path(str(result["target"])).resolve()
    try:
        target.relative_to(target_root)
    except ValueError as exc:
        raise UpgradeError("upgrade target escapes target root") from exc
    if target.name != str(result["target"]).rsplit("/", 1)[-1]:
        raise UpgradeError("upgrade target must be a plain file under target root")
    return result


def _manifest_path(value: Mapping[str, Any] | str | Path, plan: Mapping[str, Any]) -> Path | None:
    if isinstance(value, (str, Path)):
        return Path(value)
    raw = plan.get("manifest")
    return Path(str(raw)) if isinstance(raw, str) and raw else None


def apply_upgrade(
    manifest: Mapping[str, Any] | str | Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = _load_plan(manifest)
    if not force:
        raise UpgradeError("--force is required for artifact switch")
    candidate_copy = Path(str(plan.get("candidate_copy", plan["candidate"]))).resolve()
    target = Path(str(plan["target"])).resolve()
    if not candidate_copy.is_file() or _sha(candidate_copy) != plan["candidate_sha256"]:
        raise UpgradeError("prepared candidate artifact is missing or drifted")
    current = _sha(target) if target.is_file() else None
    if current != plan.get("current_sha256"):
        raise UpgradeError("upgrade target drifted after prepare")
    if dry_run:
        return {"status": "dry-run", "manifest": dict(plan)}
    fence = Path(str(plan.get("fence", target.parent / ".hippo-writer.fence")))
    fence.parent.mkdir(parents=True, exist_ok=True)
    with fence.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        # Mark the write-ahead record before switching the artifact.  A crash
        # after os.replace can then be recovered from an explicit state rather
        # than being mistaken for an untouched prepared transaction.
        manifest_path = _manifest_path(manifest, plan)
        applying = {**plan, "state": "applying", "manifest": str(manifest_path) if manifest_path else None}
        if manifest_path:
            _atomic_write(manifest_path, applying)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{target.name}.upgrade.tmp"
        try:
            shutil.copy2(candidate_copy, temp)
            if _sha(temp) != plan["candidate_sha256"]:
                raise UpgradeError("staged artifact verification failed")
            os.replace(temp, target)
            try:
                fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
    updated = {**plan, "state": "applied", "applied_sha256": _sha(target), "service_verification": "pending"}
    manifest_path = _manifest_path(manifest, plan)
    if manifest_path:
        _atomic_write(manifest_path, updated)
    return {"status": "applied", "manifest": str(manifest_path), "service_verification": "pending"}


def rollback_upgrade(manifest: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    plan = _load_plan(manifest)
    target = Path(str(plan["target"])).resolve()
    fence = Path(str(plan.get("fence", target.parent / ".hippo-writer.fence")))
    fence.parent.mkdir(parents=True, exist_ok=True)
    previous = plan.get("previous_copy")
    with fence.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if previous:
            previous_path = Path(str(previous)).resolve()
            if not previous_path.is_file():
                raise UpgradeError("previous artifact backup is missing")
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.parent / f".{target.name}.rollback.tmp"
            try:
                shutil.copy2(previous_path, temp)
                if _sha(temp) != plan.get("current_sha256"):
                    raise UpgradeError("previous artifact backup drifted")
                os.replace(temp, target)
                try:
                    parent_fd = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                except OSError:
                    pass
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
            restored = _sha(target)
        else:
            if target.exists():
                target.unlink()
            restored = None
    updated = {**plan, "state": "rolled-back", "restored_sha256": restored, "service_verification": "pending"}
    manifest_path = _manifest_path(manifest, plan)
    if manifest_path:
        _atomic_write(manifest_path, updated)
    return {"status": "rolled-back", "manifest": str(manifest_path), "restored_sha256": restored}


__all__ = ["UpgradeError", "apply_upgrade", "plan_upgrade", "prepare_upgrade", "rollback_upgrade"]
