"""Live package-default runtime for ``hippo install all --force``.

The ownership transaction handles canonical config bytes.  This module owns
the live, Hippo-exclusive surfaces around that transaction: hooks, user
systemd units/timer, writer fencing, and post-install doctor/profile probes.
It never reads or forwards provider credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import ops, paths
from .deployment import DeploymentError, InstallContext
from .dream.lock import acquire_dream_lock


_STATE_NAME = "package-runtime-state.json"
_BACKUP_NAME = "package-runtime-backup"
_HELD_DREAM_LOCKS: dict[str, Any] = {}
_UNIT_NAMES = ("paulsha-hippo-dream.service", "paulsha-hippo-dream.timer")


def _canonical_target(target_root: str | Path) -> Path:
    target = Path(target_root).expanduser().resolve()
    canonical = paths.hippo_config_root().expanduser().resolve()
    if target != canonical:
        raise DeploymentError(
            "package-default live apply requires the canonical Hippo config root; "
            "use --dry-run or a reviewed override runtime for an isolated target"
        )
    return target


def _runtime_paths(target_root: str | Path, transaction_root: str | Path) -> dict[str, Path]:
    target = _canonical_target(target_root)
    transaction = Path(transaction_root).expanduser().resolve()
    state_path = transaction / _STATE_NAME
    home = Path.home().resolve()
    memory = paths.memory_root().expanduser().resolve()
    if state_path.is_file() and not state_path.is_symlink():
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DeploymentError("package runtime snapshot is invalid") from exc
        if not isinstance(saved, Mapping):
            raise DeploymentError("package runtime snapshot root must be an object")
        saved_home = saved.get("home_root")
        saved_memory = saved.get("memory_root")
        if not isinstance(saved_home, str) or not isinstance(saved_memory, str):
            raise DeploymentError("package runtime snapshot roots are missing")
        home = Path(saved_home).expanduser().resolve()
        memory = Path(saved_memory).expanduser().resolve()
    return {
        "transaction": transaction,
        "state": state_path,
        "backup": transaction / _BACKUP_NAME,
        "config": target / "config.yaml",
        "hooks": memory / "hooks",
        "memory": memory,
        "units": home / ".config" / "systemd" / "user",
    }


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("systemctl")
    if executable is None:
        raise DeploymentError("systemctl is unavailable for package-default live install")
    completed = subprocess.run(
        [executable, "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout or "").split())[:300]
        raise DeploymentError(f"systemctl --user {' '.join(args)} failed: {detail}")
    return completed


def _unit_state(name: str) -> dict[str, bool]:
    active = _systemctl("is-active", name, check=False).stdout.strip() == "active"
    enabled = _systemctl("is-enabled", name, check=False).stdout.strip() == "enabled"
    return {"active": active, "enabled": enabled}


def _assert_exclusive_path(path: Path, label: str) -> None:
    if path.is_symlink():
        raise DeploymentError(f"{label} must not be a symlink")
    if path.exists() and not (path.is_dir() or path.is_file()):
        raise DeploymentError(f"{label} has an unsupported filesystem type")


def _assert_regular_file_or_missing(path: Path, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DeploymentError(f"{label} must be a regular file or missing")


def _file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _snapshot(paths_map: Mapping[str, Path]) -> dict[str, Any]:
    state_path = paths_map["state"]
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DeploymentError("package runtime snapshot is invalid") from exc
        if not isinstance(payload, dict):
            raise DeploymentError("package runtime snapshot root must be an object")
        return payload

    backup = paths_map["backup"]
    hooks = paths_map["hooks"]
    config = paths_map["config"]
    units = paths_map["units"]
    _assert_regular_file_or_missing(config, "canonical Hippo config")
    _assert_exclusive_path(hooks, "Hippo hooks directory")
    _assert_exclusive_path(units, "systemd user unit directory")
    backup.mkdir(parents=True, exist_ok=False)
    hooks_existed = hooks.is_dir()
    if hooks_existed:
        shutil.copytree(hooks, backup / "hooks", symlinks=True)
    config_existed = config.is_file()
    if config_existed:
        shutil.copy2(config, backup / "config.yaml")
    unit_state: dict[str, dict[str, Any]] = {}
    unit_backup = backup / "units"
    unit_backup.mkdir()
    for name in _UNIT_NAMES:
        source = units / name
        _assert_exclusive_path(source, f"systemd unit {name}")
        existed = source.is_file()
        if existed:
            shutil.copy2(source, unit_backup / name)
        unit_state[name] = {"existed": existed, **_unit_state(name)}
    payload = {
        "schema_version": "1",
        "home_root": str(paths_map["units"].parents[2]),
        "memory_root": str(paths_map["memory"]),
        "hooks_existed": hooks_existed,
        "config_existed": config_existed,
        "config_before_sha256": _file_sha256(config),
        "units": unit_state,
    }
    state_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(state_path, 0o600)
    return payload


def _load_snapshot(paths_map: Mapping[str, Path]) -> dict[str, Any]:
    state = paths_map["state"]
    if not state.is_file() or state.is_symlink():
        raise DeploymentError("package runtime snapshot is unavailable for rollback")
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("package runtime snapshot is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        raise DeploymentError("package runtime snapshot schema is invalid")
    return payload


def _stop_units(state: Mapping[str, Any]) -> None:
    units = state.get("units")
    if not isinstance(units, Mapping):
        raise DeploymentError("package runtime unit snapshot is invalid")
    for name in reversed(_UNIT_NAMES):
        row = units.get(name)
        if isinstance(row, Mapping) and row.get("active") is True:
            _systemctl("stop", name)


def _remove_exclusive(path: Path) -> None:
    _assert_exclusive_path(path, "Hippo-owned rollback surface")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _restore_hooks(paths_map: Mapping[str, Path], state: Mapping[str, Any]) -> None:
    hooks = paths_map["hooks"]
    _remove_exclusive(hooks)
    if state.get("hooks_existed") is True:
        backup = paths_map["backup"] / "hooks"
        if not backup.is_dir() or backup.is_symlink():
            raise DeploymentError("hooks rollback backup is unavailable")
        shutil.copytree(backup, hooks, symlinks=True)


def _restore_config(paths_map: Mapping[str, Path], state: Mapping[str, Any]) -> None:
    config = paths_map["config"]
    _assert_regular_file_or_missing(config, "canonical Hippo config")
    expected = state.get("config_after_sha256")
    current = _file_sha256(config)
    if isinstance(expected, str) and current != expected:
        # The filesystem rollback may already have removed a config that the
        # manifest created in this transaction.  Any other drift is an
        # operator edit and must never be overwritten by automatic rollback.
        if not (state.get("config_existed") is False and current is None):
            raise DeploymentError("canonical config changed after install mutation")
    if config.exists():
        config.unlink()
    if state.get("config_existed") is True:
        backup = paths_map["backup"] / "config.yaml"
        if not backup.is_file() or backup.is_symlink():
            raise DeploymentError("canonical config rollback backup is unavailable")
        config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, config)


def _record_config_after(paths_map: Mapping[str, Path]) -> None:
    state_path = paths_map["state"]
    state = _load_snapshot(paths_map)
    state["config_after_sha256"] = _file_sha256(paths_map["config"])
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(state_path, 0o600)


def _restore_units(paths_map: Mapping[str, Path], state: Mapping[str, Any]) -> None:
    units = paths_map["units"]
    units.mkdir(parents=True, exist_ok=True)
    rows = state.get("units")
    if not isinstance(rows, Mapping):
        raise DeploymentError("package runtime unit snapshot is invalid")
    for name in _UNIT_NAMES:
        target = units / name
        _remove_exclusive(target)
        row = rows.get(name)
        if isinstance(row, Mapping) and row.get("existed") is True:
            backup = paths_map["backup"] / "units" / name
            if not backup.is_file() or backup.is_symlink():
                raise DeploymentError(f"unit rollback backup is unavailable: {name}")
            shutil.copy2(backup, target)


def _release_dream_lock(transaction_root: Path) -> None:
    handle = _HELD_DREAM_LOCKS.pop(str(transaction_root), None)
    if handle is not None:
        handle.close()


def package_runtime_executor(
    argv: Sequence[str],
    *,
    phase: str,
    target_root: str,
    transaction_root: str,
    **_: Any,
) -> Mapping[str, Any]:
    """Execute one real package-owned live phase; no phase is an attestation no-op."""
    if tuple(argv) != ("@hippo-default-runtime@", phase):
        raise DeploymentError(f"package default runtime argv drift for phase: {phase}")
    resolved = _runtime_paths(target_root, transaction_root)
    transaction = resolved["transaction"]

    if phase == "stop_timer_service":
        if not ops._systemd_user_available():
            raise DeploymentError("systemd --user is unavailable for package-default live install")
        state = _snapshot(resolved)
        _stop_units(state)
    elif phase == "drain_writers":
        scan_ok, processes = ops._scan_hippo_processes()
        if not scan_ok or processes:
            raise DeploymentError("cannot prove that Hippo writers are drained")
        handle = acquire_dream_lock(resolved["memory"])
        if handle is None:
            raise DeploymentError("dream writer lock is still held")
        _HELD_DREAM_LOCKS[str(transaction)] = handle
    elif phase == "reinstall_hooks":
        code, message = ops._fix_backend_config(backup=False)
        if code != 0:
            raise DeploymentError(message)
        _record_config_after(resolved)
        if ops.run_install_hooks(memory_root=str(resolved["memory"]), repo_root=None) != 0:
            raise DeploymentError("hook reinstall failed")
    elif phase == "reinstall_service":
        if ops.run_install_service(enable=False, home_dir=str(Path.home())) != 0:
            raise DeploymentError("service reinstall failed")
    elif phase == "daemon_reload":
        _systemctl("daemon-reload")
    elif phase == "start_timer_service":
        _systemctl("enable", "--now", "paulsha-hippo-dream.timer")
        if _systemctl("is-active", "paulsha-hippo-dream.timer", check=False).stdout.strip() != "active":
            raise DeploymentError("dream timer is not active after install")
        _release_dream_lock(transaction)
    elif phase == "rollback_start_timer_service":
        _systemctl("stop", "paulsha-hippo-dream.timer", check=False)
    elif phase == "rollback_reinstall_service":
        _restore_units(resolved, _load_snapshot(resolved))
    elif phase == "rollback_reinstall_hooks":
        _restore_hooks(resolved, _load_snapshot(resolved))
        _restore_config(resolved, _load_snapshot(resolved))
    elif phase == "rollback_daemon_reload":
        _systemctl("daemon-reload")
    elif phase == "release_writers":
        _release_dream_lock(transaction)
    elif phase == "rollback_stop_timer_service":
        state = _load_snapshot(resolved)
        rows = state.get("units", {})
        timer = rows.get("paulsha-hippo-dream.timer", {}) if isinstance(rows, Mapping) else {}
        if isinstance(timer, Mapping) and timer.get("enabled") is True:
            _systemctl("enable", "paulsha-hippo-dream.timer")
        if isinstance(timer, Mapping) and timer.get("active") is True:
            _systemctl("start", "paulsha-hippo-dream.timer")
    else:
        raise DeploymentError(f"unsupported package runtime phase: {phase}")
    return {"ok": True, "status": "passed", "runtime_kind": "package-default", "surface": phase}


def doctor_gate(context: InstallContext) -> Mapping[str, Any]:
    _canonical_target(context.target_root)
    previous = os.environ.get("HIPPO_CONFIG_ROOT")
    os.environ["HIPPO_CONFIG_ROOT"] = str(context.target_root)
    try:
        rc = ops.run_doctor(live_probe=False, probe_profiles=False)
    finally:
        if previous is None:
            os.environ.pop("HIPPO_CONFIG_ROOT", None)
        else:
            os.environ["HIPPO_CONFIG_ROOT"] = previous
    return {"ok": rc == 0, "status": "passed" if rc == 0 else "failed"}


def profile_gate(context: InstallContext, profile_id: str) -> Mapping[str, Any]:
    _canonical_target(context.target_root)
    previous = os.environ.get("HIPPO_CONFIG_ROOT")
    os.environ["HIPPO_CONFIG_ROOT"] = str(context.target_root)
    try:
        rc = ops.run_doctor(live_probe=False, probe_profiles=True)
    finally:
        if previous is None:
            os.environ.pop("HIPPO_CONFIG_ROOT", None)
        else:
            os.environ["HIPPO_CONFIG_ROOT"] = previous
    return {
        "ok": rc == 0,
        "status": "passed" if rc == 0 else "failed",
        "profile_id": profile_id,
    }


__all__ = ["doctor_gate", "package_runtime_executor", "profile_gate"]
