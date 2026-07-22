"""Bounded, resumable artifact upgrade and rollback transactions.

The artifact switch is local and atomic, while all mutable deployment surfaces
are executed through an injected, allowlisted command runner.  The runner is
deliberately independent from the active package: it receives a minimal
environment, never a shell command, and never inherited credentials.  Every
phase is journaled before and after execution so a failed post-switch phase
automatically restores the old artifact and attempts the old hook/service
surface.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from typing import Any


class UpgradeError(ValueError):
    """The artifact transaction is unsafe, incomplete, or drifted."""


# The order is part of the transaction contract.  Keep the artifact switch
# between the writer fence and the new deployment surface verification.
PHASE_ORDER = (
    "stop_drain",
    "artifact_switch",
    "hook_reinstall",
    "service_reinstall",
    "daemon_reload",
    "service_restart",
    "project_registry_producer_wiring",
    "doctor",
    "effective_profile_verification",
)
ROLLBACK_PHASE_ORDER = (
    "rollback_hook_restore",
    "rollback_service_restore",
)
ALL_PHASES = frozenset(PHASE_ORDER + ROLLBACK_PHASE_ORDER)

_SUCCESS_STATUSES = frozenset({"ok", "passed", "success", "completed"})
_FAILURE_STATUSES = frozenset({"failed", "failure", "error", "blocked", "timeout"})
_DEFAULT_EXECUTABLES = frozenset({"hippo", "pipx", "python", "python3", "systemctl"})
_SHELL_EXECUTABLES = frozenset({"bash", "dash", "fish", "ksh", "pwsh", "sh", "zsh"})
_SHELL_TOKENS = frozenset({"-c", "--command", "--shell", "/c", "/bin/sh", "/bin/bash"})
_FORBIDDEN_TOKENS = frozenset(
    {
        "--autopilot",
        "--yolo",
        "--no-verify",
        "--dangerously-skip-permissions",
        "--allow-dangerous",
        "eval",
        "exec",
        "source",
    }
)
_PROTECTED_MARKERS = (
    ".bashrc",
    ".zshrc",
    ".profile",
    ".env",
    "api_key",
    "api-key",
    "access_token",
    "oauth",
    "password",
    "secret",
    "credential",
    "project-hippo.yaml",
    "projects.yaml",
)
_MAX_COMMANDS_PER_PHASE = 8
_MAX_TOTAL_COMMANDS = 64
_MAX_ARGC = 32
_MAX_ARG_BYTES = 4096
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_RUNNER_STDOUT_BYTES = 16 * 1024
_RUNNER_READ_BYTES = 4096
_RUNNER_POLL_SECONDS = 0.05
_SAFE_RESULT_KEYS = frozenset(
    {
        "artifact_sha256",
        "artifact_hash",
        "build_commit",
        "doctor_status",
        "effective_profile",
        "profile_id",
        "service_profile",
        "atomizer_consumed_registry",
        "atomizer_consumed_registry_contract",
        "registry_contract_consumed",
        "registry_hash",
        "registry_producer_wired",
        "registry_consumed",
        "registry_consumer_verified",
        "service_state",
        "status",
        "returncode",
        "return_code",
        "ok",
        "success",
    }
)


CommandRunner = Callable[..., Any]


class _RunnerOutputError(UpgradeError):
    """A bounded subprocess output error that retains only the exit code."""

    def __init__(self, message: str, *, returncode: int):
        super().__init__(message)
        self.returncode = int(returncode)


_SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret|"
    r"credential|private[_-]?key|refresh[_-]?token)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: object) -> None:
    if path.is_symlink():
        raise UpgradeError("manifest path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
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


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _artifact_name(value: str) -> str:
    path = Path(value)
    if path.name != value or value in {"", ".", ".."} or value.startswith("."):
        raise UpgradeError("artifact name must be a plain non-hidden filename")
    return value


def _profile_id(value: object) -> str:
    result = str(value)
    if not result or any(char in result for char in "\x00\r\n"):
        raise UpgradeError("profile_id is unsafe")
    if len(result) > 128 or any(not (char.isalnum() or char in "._:-") for char in result):
        raise UpgradeError("profile_id contains an unsafe character")
    return result


def _allowed_executables(value: Sequence[str] | None) -> tuple[str, ...]:
    configured = set(_DEFAULT_EXECUTABLES)
    if value is not None:
        if isinstance(value, (str, bytes)):
            raise UpgradeError("allowed_executables must be a sequence")
        for item in value:
            name = Path(str(item)).name
            if not name or any(char in name for char in "\x00\r\n"):
                raise UpgradeError("unsafe command executable")
            configured.add(name)
    return tuple(sorted(configured))


def _validate_argv(argv: Sequence[object], *, phase: str, allowed: frozenset[str]) -> list[str]:
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise UpgradeError(f"{phase}: command must be an argv token list")
    if not argv:
        return []
    if len(argv) > _MAX_ARGC:
        raise UpgradeError(f"{phase}: command has too many argv tokens")
    result: list[str] = []
    total = 0
    for raw in argv:
        if not isinstance(raw, str) or not raw:
            raise UpgradeError(f"{phase}: command tokens must be non-empty strings")
        if len(raw.encode("utf-8")) > 512:
            raise UpgradeError(f"{phase}: command token is too long")
        if any(marker in raw for marker in ("\x00", "\r", "\n", ";", "|", "&", "`", "$(", ">", "<")):
            raise UpgradeError(f"{phase}: shell syntax is not allowed")
        lowered = raw.casefold()
        if lowered in {token.casefold() for token in _SHELL_TOKENS}:
            raise UpgradeError(f"{phase}: shell command mode is not allowed")
        if lowered in {token.casefold() for token in _FORBIDDEN_TOKENS}:
            raise UpgradeError(f"{phase}: unsafe command token is not allowed")
        if any(marker in lowered for marker in _PROTECTED_MARKERS):
            raise UpgradeError(f"{phase}: credential, shell-rc, or protected data path is not allowed")
        result.append(raw)
        total += len(raw.encode("utf-8"))
    if total > _MAX_ARG_BYTES:
        raise UpgradeError(f"{phase}: command argv is too large")
    executable = Path(result[0]).name
    if executable.casefold() in _SHELL_EXECUTABLES:
        raise UpgradeError(f"{phase}: shell executable is not allowed")
    if executable not in allowed:
        raise UpgradeError(f"{phase}: executable is not allowlisted")
    if executable in {"python", "python3"} and any(token in {"-c", "-mcompile"} for token in result[1:]):
        raise UpgradeError(f"{phase}: inline Python execution is not allowed")
    if executable == "systemctl" and not any(
        token in {"daemon-reload", "disable", "enable", "is-active", "is-enabled", "reload", "restart", "show", "start", "stop"}
        for token in result[1:]
    ):
        raise UpgradeError(f"{phase}: systemctl action is not allowlisted")
    return result


def _normalize_commands(
    raw: object,
    *,
    phase: str,
    allowed: frozenset[str],
) -> list[list[str]]:
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise UpgradeError(f"{phase}: commands must be argv lists")
    if not raw:
        return []
    # A flat sequence is one argv; a nested sequence is several argv lists.
    if all(isinstance(item, str) for item in raw):
        commands: list[Sequence[object]] = [raw]
    else:
        commands = list(raw)  # type: ignore[assignment]
    if len(commands) > _MAX_COMMANDS_PER_PHASE:
        raise UpgradeError(f"{phase}: too many commands")
    return [_validate_argv(command, phase=phase, allowed=allowed) for command in commands]


def _normalize_command_map(
    raw: Mapping[str, object] | None,
    *,
    phases: Sequence[str],
    allowed: frozenset[str],
) -> dict[str, list[list[str]]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise UpgradeError("phase commands must be a mapping")
    unknown = set(raw) - set(phases)
    if unknown:
        raise UpgradeError(f"unknown upgrade phase: {sorted(unknown)[0]}")
    return {
        phase: _normalize_commands(raw.get(phase), phase=phase, allowed=allowed)
        for phase in phases
    }


def _phase_evidence(
    phase_commands: Mapping[str, Sequence[Sequence[str]]],
    phases: Sequence[str],
) -> dict[str, dict[str, Any]]:
    return {
        phase: {
            "status": "pending",
            "commands": [list(command) for command in phase_commands.get(phase, ())],
            "attempts": [],
        }
        for phase in phases
    }


def _command_limits(value: Mapping[str, object] | None) -> dict[str, Any]:
    value = value or {}
    try:
        timeout = float(value.get("timeout_seconds", 60.0))
    except (TypeError, ValueError) as exc:
        raise UpgradeError("command timeout must be numeric") from exc
    try:
        max_commands = int(value.get("max_commands", _MAX_TOTAL_COMMANDS))
    except (TypeError, ValueError) as exc:
        raise UpgradeError("max_commands must be an integer") from exc
    if not 0 < timeout <= _MAX_TIMEOUT_SECONDS:
        raise UpgradeError("command timeout is outside the bounded range")
    if not 0 < max_commands <= _MAX_TOTAL_COMMANDS:
        raise UpgradeError("max_commands is outside the bounded range")
    return {"timeout_seconds": timeout, "max_commands": max_commands}


def _plan_command_maps(plan: Mapping[str, Any]) -> tuple[dict[str, list[list[str]]], dict[str, list[list[str]]]]:
    allowed = frozenset(str(item) for item in plan.get("allowed_executables", _DEFAULT_EXECUTABLES))
    return (
        _normalize_command_map(plan.get("phase_commands"), phases=PHASE_ORDER, allowed=allowed),
        _normalize_command_map(plan.get("rollback_commands"), phases=ROLLBACK_PHASE_ORDER, allowed=allowed),
    )


def plan_upgrade(
    candidate: str | Path,
    *,
    target_root: str | Path,
    profile_id: str = "candidate",
    artifact_name: str = "hippo.whl",
    phase_commands: Mapping[str, object] | None = None,
    rollback_commands: Mapping[str, object] | None = None,
    command_timeout: float = 60.0,
    max_commands: int = _MAX_TOTAL_COMMANDS,
    allowed_executables: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Create a hash-pinned plan without mutating the target or transaction.

    ``phase_commands`` and ``rollback_commands`` are tokenized argv lists.  A
    missing command is intentional metadata for dry-run, but apply rejects an
    unconfigured external phase before switching the artifact.  This keeps the
    CLI skeleton safe while allowing callers/tests to inject a bounded runner.
    """

    source_input = Path(candidate).expanduser()
    if source_input.is_symlink():
        raise UpgradeError("candidate artifact is not a regular file")
    source = source_input.resolve()
    if not source.is_file():
        raise UpgradeError("candidate artifact is not a regular file")
    name = _artifact_name(artifact_name)
    target_dir_input = Path(target_root).expanduser()
    if target_dir_input.is_symlink():
        raise UpgradeError("artifact target root must be a real directory")
    target_dir = target_dir_input.resolve()
    if target_dir.exists() and not target_dir.is_dir():
        raise UpgradeError("artifact target root must be a real directory")
    target = target_dir / name
    if (target_dir / name).is_symlink():
        raise UpgradeError("upgrade target must not be a symlink")
    current_hash = _sha(target) if target.is_file() else None
    profile = _profile_id(profile_id)
    allowed = _allowed_executables(allowed_executables)
    commands = _normalize_command_map(phase_commands, phases=PHASE_ORDER, allowed=frozenset(allowed))
    rollback = _normalize_command_map(
        rollback_commands,
        phases=ROLLBACK_PHASE_ORDER,
        allowed=frozenset(allowed),
    )
    limits = _command_limits({"timeout_seconds": command_timeout, "max_commands": max_commands})
    total_commands = sum(len(items) for items in commands.values()) + sum(len(items) for items in rollback.values())
    if total_commands > limits["max_commands"]:
        raise UpgradeError("phase command count exceeds the transaction budget")
    return {
        "schema_version": "2",
        "candidate": str(source),
        "candidate_sha256": _sha(source),
        "target_root": str(target_dir),
        "target": str(target),
        "current_sha256": current_hash,
        "profile_id": profile,
        "phase_order": list(PHASE_ORDER),
        "rollback_phase_order": list(ROLLBACK_PHASE_ORDER),
        "phase_commands": commands,
        "rollback_commands": rollback,
        "allowed_executables": list(allowed),
        "command_limits": limits,
        "state": "planned",
        "write_ahead": False,
    }


def _load_plan(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    result = dict(value) if isinstance(value, Mapping) else _read(value)
    required = ("candidate_sha256", "target", "target_root", "candidate")
    if any(not result.get(key) for key in required):
        raise UpgradeError("upgrade manifest is incomplete")
    candidate_hash = str(result["candidate_sha256"])
    if len(candidate_hash) != 64 or any(char not in "0123456789abcdef" for char in candidate_hash.lower()):
        raise UpgradeError("candidate artifact hash is invalid")
    target_root_input = Path(str(result["target_root"])).expanduser()
    target_input = Path(str(result["target"])).expanduser()
    if target_root_input.is_symlink() or target_input.is_symlink():
        raise UpgradeError("upgrade target paths must not be symlinks")
    target_root = target_root_input.resolve()
    target = target_input.resolve()
    try:
        target.relative_to(target_root)
    except ValueError as exc:
        raise UpgradeError("upgrade target escapes target root") from exc
    if target.parent != target_root or target.name != str(result["target"]).rsplit("/", 1)[-1]:
        raise UpgradeError("upgrade target must be a plain file directly under target root")
    if target.is_symlink():
        raise UpgradeError("upgrade target must not be a symlink")
    result["profile_id"] = _profile_id(result.get("profile_id", "candidate"))
    result["allowed_executables"] = list(_allowed_executables(result.get("allowed_executables")))
    result["command_limits"] = _command_limits(result.get("command_limits"))
    result["phase_commands"], result["rollback_commands"] = _plan_command_maps(result)
    total_commands = sum(len(items) for items in result["phase_commands"].values()) + sum(
        len(items) for items in result["rollback_commands"].values()
    )
    if total_commands > result["command_limits"]["max_commands"]:
        raise UpgradeError("phase command count exceeds the transaction budget")
    return result


def _manifest_path(value: Mapping[str, Any] | str | Path, plan: Mapping[str, Any]) -> Path | None:
    if isinstance(value, (str, Path)):
        return Path(value).expanduser().absolute()
    raw = plan.get("manifest")
    if isinstance(raw, str) and raw:
        return Path(raw).expanduser().absolute()
    root = plan.get("transaction_root")
    if isinstance(root, str) and root:
        return Path(root).expanduser().absolute() / "upgrade.json"
    return None


def _persist(path: Path | None, manifest: Mapping[str, Any]) -> None:
    if path is None:
        raise UpgradeError("upgrade transaction requires a durable manifest path")
    _atomic_write(path, dict(manifest))


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle, source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, handle, length=1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copystat(source, name, follow_symlinks=False)
        os.replace(name, destination)
        try:
            parent_fd = os.open(destination.parent, os.O_RDONLY)
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


def _pending_evidence(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phases": _phase_evidence(plan["phase_commands"], PHASE_ORDER),
        "rollback_phases": _phase_evidence(plan["rollback_commands"], ROLLBACK_PHASE_ORDER),
    }


def _same_plan(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("candidate_sha256") == right.get("candidate_sha256")
        and left.get("target") == right.get("target")
        and left.get("current_sha256") == right.get("current_sha256")
        and left.get("profile_id") == right.get("profile_id")
        and left.get("phase_commands") == right.get("phase_commands")
        and left.get("rollback_commands") == right.get("rollback_commands")
    )


def prepare_upgrade(
    plan: Mapping[str, Any] | str | Path,
    *,
    transaction_root: str | Path | None = None,
) -> dict[str, Any]:
    """Freeze candidate and old artifact, then fsync a write-ahead manifest."""

    source = _load_plan(plan)
    candidate = Path(str(source["candidate"])).expanduser().resolve()
    target = Path(str(source["target"])).expanduser().resolve()
    target_root = Path(str(source["target_root"])).expanduser().resolve()
    if not candidate.is_file() or candidate.is_symlink() or _sha(candidate) != source["candidate_sha256"]:
        raise UpgradeError("candidate artifact drifted before prepare")
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise UpgradeError("upgrade target is not a regular file")
    current = _sha(target) if target.is_file() else None
    if current != source.get("current_sha256"):
        raise UpgradeError("upgrade target drifted before prepare")
    if transaction_root is None:
        root = target_root.parent / f".{target_root.name}.hippo-upgrade"
    else:
        root = Path(transaction_root).expanduser().resolve()
    if _is_within(root, target_root) or root == target_root:
        raise UpgradeError("transaction root must be outside the mutable target root")
    if root.exists() and root.is_symlink():
        raise UpgradeError("transaction root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "upgrade.json"
    if manifest_path.exists():
        existing = _load_plan(_read(manifest_path))
        if not _same_plan(existing, source):
            raise UpgradeError("transaction root already contains a different upgrade")
        existing["manifest"] = str(manifest_path)
        if existing.get("state") in {"prepared", "applying", "applied", "rolled-back", "rollback-blocked"}:
            return existing
        raise UpgradeError("transaction manifest is not resumable")

    candidate_copy = root / "candidate.whl"
    previous_copy = root / "previous.whl"
    fence = root / "writer.fence"
    manifest: dict[str, Any] = {
        **source,
        "state": "preparing",
        "transaction_root": str(root),
        "candidate_copy": str(candidate_copy),
        "previous_copy": str(previous_copy) if current is not None else None,
        "previous_sha256": current,
        "fence": str(fence),
        "manifest": str(manifest_path),
        "write_ahead": True,
        "prepared_at": _utc_now(),
        "evidence": _pending_evidence(source),
        "history": [{"event": "prepare-start", "at": _utc_now(), "state": "preparing"}],
    }
    # The journal exists before any transaction-root copy is made.
    _persist(manifest_path, manifest)
    lock_path = root / "prepare.lock"
    try:
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            # Re-read the target under the transaction lock immediately before
            # copying the old artifact.
            current = _sha(target) if target.is_file() else None
            if current != source.get("current_sha256"):
                raise UpgradeError("upgrade target drifted during prepare")
            _copy_atomic(candidate, candidate_copy)
            if _sha(candidate_copy) != source["candidate_sha256"]:
                raise UpgradeError("candidate copy verification failed")
            if current is not None:
                _copy_atomic(target, previous_copy)
                if _sha(previous_copy) != current:
                    raise UpgradeError("previous artifact backup verification failed")
            manifest["state"] = "prepared"
            manifest["prepared_sha256"] = source["candidate_sha256"]
            manifest["history"].append({"event": "prepare-complete", "at": _utc_now(), "state": "prepared"})
            _persist(manifest_path, manifest)
    except Exception as exc:
        manifest["state"] = "prepare-failed"
        manifest["failure"] = {"phase": "prepare", "reason": str(exc), "at": _utc_now()}
        try:
            _persist(manifest_path, manifest)
        except UpgradeError:
            pass
        if isinstance(exc, UpgradeError):
            raise
        raise UpgradeError("upgrade prepare failed") from exc
    return dict(manifest)


def _safe_result_details(result: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in _SAFE_RESULT_KEYS:
        if key not in result:
            continue
        value = result[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            details[key] = value
    return details


def _safe_reason(value: object) -> str:
    """Keep bounded failure evidence without persisting credential-shaped text."""

    text = str(value)
    text = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|credential)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer <redacted>", text)
    return text[:512]


def _runner_exception_details(exc: BaseException) -> dict[str, Any]:
    """Persist only a numeric exit code from a runner-side exception."""

    value = getattr(exc, "returncode", None)
    if isinstance(value, bool):
        return {}
    try:
        return {"returncode": int(value)} if value is not None else {}
    except (TypeError, ValueError, OverflowError):
        return {}


def _normalize_result(result: Any) -> tuple[bool, dict[str, Any], str | None]:
    if isinstance(result, bool):
        return result, {"ok": result}, None if result else "runner returned false"
    if isinstance(result, int):
        return result == 0, {"returncode": result}, None if result == 0 else f"runner returned {result}"
    if isinstance(result, subprocess.CompletedProcess):
        code = int(result.returncode)
        return code == 0, {"returncode": code}, None if code == 0 else f"runner returned {code}"
    if isinstance(result, Mapping):
        details = _safe_result_details(result)
        status = str(result.get("status", "")).casefold()
        returncode = result.get("returncode", result.get("return_code"))
        if returncode is not None:
            try:
                returncode = int(returncode)
            except (TypeError, ValueError):
                return False, details, "runner returncode is invalid"
            details["returncode"] = returncode
            if returncode != 0:
                return False, details, f"runner returned {returncode}"
        if result.get("ok") is False or result.get("success") is False or status in _FAILURE_STATUSES:
            return False, details, status if status in _FAILURE_STATUSES else "runner reported failure"
        if result.get("ok") is True or result.get("success") is True or status in _SUCCESS_STATUSES or returncode == 0:
            return True, details, None
        return False, details, "runner returned no success evidence"
    return False, {}, "runner returned an unsupported result"


def _invoke_runner(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    phase: str,
    profile_id: str,
    timeout: float,
    transaction_root: Path,
) -> Any:
    callback = getattr(runner, "run", runner)
    env = {
        "PATH": os.defpath,
        "LC_ALL": "C",
        "HIPPO_UPGRADE_PHASE": phase,
        "HIPPO_PROFILE_ID": profile_id,
    }
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        signature = None
    if signature is None:
        return callback(
            list(argv),
            phase=phase,
            profile_id=profile_id,
            timeout=timeout,
            env=env,
            cwd=str(transaction_root),
        )
    parameters = signature.parameters
    first = next(iter(parameters.values()), None)
    first_is_phase = first is not None and first.name in {"phase", "name"}
    kwargs = {
        key: value
        for key, value in {
            "phase": phase,
            "profile_id": profile_id,
            "timeout": timeout,
            "env": env,
            "cwd": str(transaction_root),
        }.items()
        if key in parameters and not (first_is_phase and key == "phase")
    }
    if first_is_phase:
        return callback(phase, list(argv), **kwargs)
    return callback(list(argv), **kwargs)


def _bounded_subprocess(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str],
    cwd: str,
) -> tuple[int, bytes, bool]:
    """Run one argv and retain at most ``_MAX_RUNNER_STDOUT_BYTES + 1`` bytes."""

    process = subprocess.Popen(
        list(argv),
        shell=False,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        env=dict(env),
    )
    stdout_pipe = process.stdout
    selector = selectors.DefaultSelector()
    if stdout_pipe is not None:
        selector.register(stdout_pipe, selectors.EVENT_READ)
    captured = bytearray()
    oversized = False
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=1.0)
                raise subprocess.TimeoutExpired(list(argv), timeout)
            events = selector.select(min(remaining, _RUNNER_POLL_SECONDS))
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, _RUNNER_READ_BYTES)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if len(captured) <= _MAX_RUNNER_STDOUT_BYTES:
                    room = _MAX_RUNNER_STDOUT_BYTES + 1 - len(captured)
                    captured.extend(chunk[:room])
                    oversized = oversized or len(captured) > _MAX_RUNNER_STDOUT_BYTES
        return int(process.wait()), bytes(captured), oversized
    finally:
        selector.close()
        if stdout_pipe is not None and not stdout_pipe.closed:
            stdout_pipe.close()


def _parse_runner_stdout(
    stdout: bytes,
    *,
    phase: str,
    returncode: int,
) -> dict[str, Any]:
    """Parse only a small, scalar, allowlisted JSON object from stdout."""

    if len(stdout) > _MAX_RUNNER_STDOUT_BYTES:
        raise _RunnerOutputError(
            f"{phase}: command stdout exceeds the bounded JSON limit",
            returncode=returncode,
        )
    if not stdout.strip():
        return {}
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _RunnerOutputError(
            f"{phase}: command stdout is not UTF-8 JSON",
            returncode=returncode,
        ) from exc
    if _SENSITIVE_OUTPUT_RE.search(text):
        raise _RunnerOutputError(
            f"{phase}: command stdout contains protected data",
            returncode=returncode,
        )

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        payload = json.loads(text, parse_constant=reject_constant)
    except (TypeError, ValueError) as exc:
        raise _RunnerOutputError(
            f"{phase}: command stdout is not valid JSON",
            returncode=returncode,
        ) from exc
    if not isinstance(payload, dict):
        raise _RunnerOutputError(
            f"{phase}: command stdout JSON must be an object",
            returncode=returncode,
        )
    if set(payload) - _SAFE_RESULT_KEYS:
        raise _RunnerOutputError(
            f"{phase}: command stdout JSON contains unsupported fields",
            returncode=returncode,
        )
    for value in payload.values():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise _RunnerOutputError(
                f"{phase}: command stdout JSON contains unsupported values",
                returncode=returncode,
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise _RunnerOutputError(
                f"{phase}: command stdout JSON contains a non-finite number",
                returncode=returncode,
            )
    return _safe_result_details(payload)


def _default_runner(
    argv: Sequence[str],
    *,
    phase: str,
    profile_id: str,
    timeout: float,
    env: Mapping[str, str],
    cwd: str,
) -> Mapping[str, Any]:
    if not argv:
        raise UpgradeError(f"{phase}: no allowlisted command is configured")
    try:
        returncode, stdout, oversized = _bounded_subprocess(
            argv,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpgradeError(f"{phase}: command timed out") from exc
    if oversized:
        raise _RunnerOutputError(
            f"{phase}: command stdout exceeds the bounded JSON limit",
            returncode=returncode,
        )
    result = _parse_runner_stdout(stdout, phase=phase, returncode=returncode)
    # The process exit code is authoritative even if a command emitted a
    # contradictory JSON returncode field.
    result["returncode"] = returncode
    return result


def _record_history(manifest: dict[str, Any], event: str, **fields: Any) -> None:
    history = manifest.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        manifest["history"] = history
    history.append({"event": event, "at": _utc_now(), **fields})


def _phase_failure(phase: str, reason: str) -> UpgradeError:
    return UpgradeError(f"upgrade phase {phase} failed closed: {reason}")


def _validate_identity(phase: str, details: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    expected_hash = str(plan["candidate_sha256"])
    expected_profile = str(plan["profile_id"])
    reported_profile = details.get(
        "profile_id",
        details.get("effective_profile", details.get("service_profile")),
    )
    if reported_profile is not None and str(reported_profile) != expected_profile:
        raise _phase_failure(phase, "service/profile identity mismatch")
    reported_hash = details.get("artifact_sha256", details.get("artifact_hash"))
    if reported_hash is not None and str(reported_hash) != expected_hash:
        raise _phase_failure(phase, "service/artifact identity mismatch")


def _validate_phase_attestation(phase: str, details: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    _validate_identity(phase, details, plan)
    if phase == "project_registry_producer_wiring":
        wired = details.get("registry_producer_wired") is True
        consumed = any(
            details.get(key) is True
            for key in (
                "registry_contract_consumed",
                "registry_consumed",
                "registry_consumer_verified",
                "atomizer_consumed_registry",
                "atomizer_consumed_registry_contract",
            )
        )
        if not wired or not consumed:
            raise _phase_failure(phase, "registry producer/consumer attestation is incomplete")
    if phase == "effective_profile_verification":
        profile = details.get(
            "profile_id",
            details.get("effective_profile", details.get("service_profile")),
        )
        artifact = details.get("artifact_sha256", details.get("artifact_hash"))
        if str(profile) != str(plan["profile_id"]) or str(artifact) != str(plan["candidate_sha256"]):
            raise _phase_failure(phase, "effective service profile or artifact is not the candidate")


def _execute_phase(
    manifest: dict[str, Any],
    *,
    phase: str,
    runner: CommandRunner,
    runner_is_default: bool,
    transaction_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    evidence = manifest["evidence"]["phases"][phase]
    evidence["status"] = "running"
    evidence["started_at"] = _utc_now()
    _record_history(manifest, "phase-start", phase=phase)
    _persist(manifest_path, manifest)
    commands = evidence.get("commands", [])
    if not commands and phase != "artifact_switch" and runner_is_default:
        raise _phase_failure(phase, "phase has no configured command")
    if not commands:
        commands = [[]]
    aggregate: dict[str, Any] = {}
    for index, argv in enumerate(commands):
        started = _utc_now()
        try:
            raw = _invoke_runner(
                runner,
                argv,
                phase=phase,
                profile_id=str(manifest["profile_id"]),
                timeout=float(manifest["command_limits"]["timeout_seconds"]),
                transaction_root=transaction_root,
            )
        except Exception as exc:
            reason = _safe_reason(exc) if isinstance(exc, UpgradeError) else f"runner exception: {type(exc).__name__}"
            attempt = {
                "index": index,
                "argv": list(argv),
                "status": "failed",
                "result": _runner_exception_details(exc),
                "reason": reason,
                "started_at": started,
                "finished_at": _utc_now(),
            }
            evidence.setdefault("attempts", []).append(attempt)
            evidence["status"] = "failed"
            evidence["failure"] = {"reason": reason, "at": _utc_now()}
            _record_history(manifest, "phase-failed", phase=phase, reason=reason)
            _persist(manifest_path, manifest)
            raise _phase_failure(phase, reason) from exc
        ok, details, reason = _normalize_result(raw)
        attempt = {
            "index": index,
            "argv": list(argv),
            "status": "passed" if ok else "failed",
            "result": details,
            "started_at": started,
            "finished_at": _utc_now(),
        }
        evidence.setdefault("attempts", []).append(attempt)
        if not ok:
            evidence["status"] = "failed"
            safe_reason = _safe_reason(reason or "runner failure")
            evidence["failure"] = {"reason": safe_reason, "at": _utc_now()}
            _record_history(manifest, "phase-failed", phase=phase, reason=safe_reason)
            _persist(manifest_path, manifest)
            raise _phase_failure(phase, safe_reason)
        aggregate.update(details)
        _persist(manifest_path, manifest)
    try:
        _validate_phase_attestation(phase, aggregate, manifest)
    except Exception as exc:
        reason = _safe_reason(exc)
        evidence["status"] = "failed"
        evidence["failure"] = {"reason": reason, "at": _utc_now()}
        _record_history(manifest, "phase-failed", phase=phase, reason=reason)
        _persist(manifest_path, manifest)
        raise _phase_failure(phase, reason) from exc
    evidence["status"] = "passed"
    evidence["finished_at"] = _utc_now()
    evidence["result"] = aggregate
    _record_history(manifest, "phase-passed", phase=phase)
    _persist(manifest_path, manifest)
    return aggregate


def _switch_artifact(manifest: dict[str, Any]) -> None:
    target = Path(str(manifest["target"])).resolve()
    candidate_copy = Path(str(manifest["candidate_copy"])).resolve()
    expected_old = manifest.get("current_sha256")
    current = _sha(target) if target.is_file() else None
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise UpgradeError("upgrade target is not a regular file")
    if current == manifest["candidate_sha256"]:
        return
    if current != expected_old:
        raise UpgradeError("upgrade target drifted after prepare")
    if not candidate_copy.is_file() or candidate_copy.is_symlink() or _sha(candidate_copy) != manifest["candidate_sha256"]:
        raise UpgradeError("prepared candidate artifact is missing or drifted")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".upgrade.tmp", dir=str(target.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        with temp.open("wb") as handle, candidate_copy.open("rb") as source:
            shutil.copyfileobj(source, handle, length=1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        if _sha(temp) != manifest["candidate_sha256"]:
            raise UpgradeError("staged artifact verification failed")
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


def _restore_artifact(manifest: Mapping[str, Any]) -> dict[str, Any]:
    target = Path(str(manifest["target"])).resolve()
    candidate_hash = str(manifest["candidate_sha256"])
    current_hash = manifest.get("current_sha256")
    actual = _sha(target) if target.is_file() else None
    if actual == current_hash:
        return {"status": "already-restored", "restored_sha256": actual}
    if actual not in {candidate_hash, manifest.get("applied_sha256")}:
        raise UpgradeError("rollback blocked by target artifact drift")
    previous = manifest.get("previous_copy")
    if previous:
        previous_path = Path(str(previous)).resolve()
        expected_previous = manifest.get("previous_sha256", current_hash)
        if not previous_path.is_file() or previous_path.is_symlink() or _sha(previous_path) != expected_previous:
            raise UpgradeError("previous artifact backup is missing or drifted")
        _copy_atomic(previous_path, target)
    else:
        if target.exists():
            target.unlink()
    restored = _sha(target) if target.is_file() else None
    if restored != current_hash:
        raise UpgradeError("artifact rollback hash verification failed")
    return {"status": "restored", "restored_sha256": restored}


def _run_rollback_phases(
    manifest: dict[str, Any],
    *,
    runner: CommandRunner,
    runner_is_default: bool,
    transaction_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for phase in ROLLBACK_PHASE_ORDER:
        evidence = manifest["evidence"]["rollback_phases"][phase]
        if evidence.get("status") == "passed":
            results[phase] = evidence.get("result", {})
            continue
        evidence["status"] = "running"
        evidence["started_at"] = _utc_now()
        _record_history(manifest, "rollback-phase-start", phase=phase)
        _persist(manifest_path, manifest)
        commands = evidence.get("commands", [])
        if not commands and runner_is_default:
            evidence["status"] = "blocked"
            evidence["failure"] = {"reason": "restore command is not configured", "at": _utc_now()}
            _record_history(manifest, "rollback-phase-failed", phase=phase, reason="restore command is not configured")
            _persist(manifest_path, manifest)
            results[phase] = {"status": "blocked"}
            continue
        if not commands:
            commands = [[]]
        aggregate: dict[str, Any] = {}
        failed = False
        for index, argv in enumerate(commands):
            try:
                raw = _invoke_runner(
                    runner,
                    argv,
                    phase=phase,
                    profile_id=str(manifest["profile_id"]),
                    timeout=float(manifest["command_limits"]["timeout_seconds"]),
                    transaction_root=transaction_root,
                )
                ok, details, reason = _normalize_result(raw)
            except Exception as exc:
                reason = _safe_reason(exc) if isinstance(exc, UpgradeError) else f"runner exception: {type(exc).__name__}"
                ok, details = False, _runner_exception_details(exc)
            attempt = {
                "index": index,
                "argv": list(argv),
                "status": "passed" if ok else "failed",
                "result": details,
                "finished_at": _utc_now(),
            }
            evidence.setdefault("attempts", []).append(attempt)
            if not ok:
                failed = True
                evidence["failure"] = {"reason": _safe_reason(reason or "runner failure"), "at": _utc_now()}
                break
            aggregate.update(details)
        if failed:
            evidence["status"] = "blocked"
            _record_history(manifest, "rollback-phase-failed", phase=phase, reason=evidence["failure"]["reason"])
            results[phase] = {"status": "blocked", "reason": evidence["failure"]["reason"]}
        else:
            evidence["status"] = "passed"
            evidence["finished_at"] = _utc_now()
            evidence["result"] = aggregate
            _record_history(manifest, "rollback-phase-passed", phase=phase)
            results[phase] = aggregate
        _persist(manifest_path, manifest)
    return results


def _auto_rollback(
    manifest: dict[str, Any],
    *,
    runner: CommandRunner,
    runner_is_default: bool,
    transaction_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    rollback: dict[str, Any] = {"started_at": _utc_now(), "artifact": {}, "phases": {}}
    try:
        rollback["artifact"] = _restore_artifact(manifest)
    except Exception as exc:
        rollback["artifact"] = {"status": "blocked", "reason": _safe_reason(exc)}
    rollback["phases"] = _run_rollback_phases(
        manifest,
        runner=runner,
        runner_is_default=runner_is_default,
        transaction_root=transaction_root,
        manifest_path=manifest_path,
    )
    artifact_ok = rollback["artifact"].get("status") in {"restored", "already-restored"}
    phases_ok = all(value.get("status", "ok") != "blocked" for value in rollback["phases"].values())
    rollback["status"] = "rolled-back" if artifact_ok and phases_ok else "rollback-blocked"
    rollback["finished_at"] = _utc_now()
    return rollback


def _reset_for_retry(manifest: dict[str, Any]) -> None:
    for phase in PHASE_ORDER:
        evidence = manifest["evidence"]["phases"][phase]
        # A rollback may have restored the old hooks/service.  Even phases
        # that passed in the failed attempt therefore need a fresh run; their
        # prior attempts remain in the manifest as audit evidence.
        evidence["status"] = "pending"
        evidence.pop("failure", None)
        evidence.pop("result", None)
        evidence.pop("started_at", None)
        evidence.pop("finished_at", None)
        evidence.pop("internal_switch", None)
    _record_history(manifest, "retry-start", state="prepared")
    manifest["state"] = "prepared"


def _require_complete_command_plan(manifest: Mapping[str, Any]) -> None:
    """Reject an incomplete external phase plan before artifact mutation."""

    missing = [
        phase
        for phase in PHASE_ORDER
        if not manifest["evidence"]["phases"][phase].get("commands")
    ]
    missing += [
        phase
        for phase in ROLLBACK_PHASE_ORDER
        if not manifest["evidence"]["rollback_phases"][phase].get("commands")
    ]
    if missing:
        raise UpgradeError(
            "upgrade command plan is incomplete before mutation: " + ", ".join(missing)
        )


def apply_upgrade(
    manifest: Mapping[str, Any] | str | Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Run the ordered deployment transaction with automatic compensation."""

    plan = _load_plan(manifest)
    if not force and not dry_run:
        raise UpgradeError("--force is required for artifact switch")
    manifest_path = _manifest_path(manifest, plan)
    if dry_run:
        candidate_copy = plan.get("candidate_copy")
        if candidate_copy and Path(str(candidate_copy)).is_file() and _sha(Path(str(candidate_copy))) != plan["candidate_sha256"]:
            raise UpgradeError("prepared candidate artifact is missing or drifted")
        return {
            "status": "dry-run",
            "mutation": False,
            "phase_order": list(PHASE_ORDER),
            "rollback_phase_order": list(ROLLBACK_PHASE_ORDER),
            "manifest": dict(plan),
        }
    if manifest_path is None or not manifest_path.is_file():
        raise UpgradeError("apply requires a prepared write-ahead manifest")
    durable = _load_plan(_read(manifest_path))
    durable["manifest"] = str(manifest_path)
    if durable.get("state") == "applied":
        target = Path(str(durable["target"]))
        if target.is_file() and _sha(target) == durable["candidate_sha256"] and all(
            durable["evidence"]["phases"][phase].get("status") == "passed" for phase in PHASE_ORDER
        ):
            return {"status": "already-applied", "manifest": str(manifest_path), "idempotent": True}
        raise UpgradeError("applied transaction no longer matches its candidate or evidence")
    if durable.get("state") in {"rolled-back", "rollback-blocked"}:
        _reset_for_retry(durable)
        _persist(manifest_path, durable)
    if durable.get("state") not in {"prepared", "applying"}:
        raise UpgradeError("upgrade manifest is not prepared for apply")
    _require_complete_command_plan(durable)
    if Path(str(durable.get("candidate_copy", ""))).is_file() is False or _sha(Path(str(durable["candidate_copy"]))) != durable["candidate_sha256"]:
        raise UpgradeError("prepared candidate artifact is missing or drifted")
    root = Path(str(durable["transaction_root"])).resolve()
    target_root = Path(str(durable["target_root"])).resolve()
    if _is_within(root, target_root) or root == target_root:
        raise UpgradeError("transaction root must be outside the mutable target root")
    runner_is_default = runner is None
    effective_runner = _default_runner if runner is None else runner
    fence = Path(str(durable["fence"])).resolve()
    fence.parent.mkdir(parents=True, exist_ok=True)
    with fence.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        durable["state"] = "applying"
        durable["apply_started_at"] = _utc_now()
        _record_history(durable, "apply-start", state="applying")
        _persist(manifest_path, durable)
        failed_phase = "unknown"
        try:
            for phase in PHASE_ORDER:
                failed_phase = phase
                if durable["evidence"]["phases"][phase].get("status") == "passed":
                    continue
                if phase == "artifact_switch":
                    _switch_artifact(durable)
                    durable["applied_sha256"] = durable["candidate_sha256"]
                    durable["evidence"]["phases"][phase]["internal_switch"] = {
                        "status": "passed",
                        "artifact_sha256": durable["candidate_sha256"],
                    }
                    _persist(manifest_path, durable)
                _execute_phase(
                    durable,
                    phase=phase,
                    runner=effective_runner,
                    runner_is_default=runner_is_default,
                    transaction_root=root,
                    manifest_path=manifest_path,
                )
            target = Path(str(durable["target"]))
            if not target.is_file() or _sha(target) != durable["candidate_sha256"]:
                raise UpgradeError("candidate artifact hash is not present after apply")
            durable["state"] = "applied"
            durable["applied_at"] = _utc_now()
            _record_history(durable, "apply-complete", state="applied")
            _persist(manifest_path, durable)
            return {
                "status": "applied",
                "manifest": str(manifest_path),
                "artifact_sha256": durable["candidate_sha256"],
                "profile_id": durable["profile_id"],
                "phase_order": list(PHASE_ORDER),
            }
        except Exception as exc:
            reason = _safe_reason(exc)
            phase_evidence = durable["evidence"]["phases"][failed_phase]
            if phase_evidence.get("status") != "passed":
                phase_evidence["status"] = "failed"
                phase_evidence.setdefault("failure", {"reason": reason, "at": _utc_now()})
            durable["failure"] = {"phase": failed_phase, "reason": reason, "at": _utc_now()}
            _record_history(durable, "apply-failed", phase=failed_phase, reason=reason)
            _persist(manifest_path, durable)
            rollback = _auto_rollback(
                durable,
                runner=effective_runner,
                runner_is_default=runner_is_default,
                transaction_root=root,
                manifest_path=manifest_path,
            )
            durable["rollback"] = rollback
            durable["state"] = rollback["status"]
            _record_history(durable, "apply-rollback-complete", state=durable["state"])
            _persist(manifest_path, durable)
            raise UpgradeError(
                f"upgrade phase {failed_phase} failed; automatic {rollback['status']}"
            ) from exc


def rollback_upgrade(
    manifest: Mapping[str, Any] | str | Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Restore the pinned old artifact and attempt old hook/service restore."""

    plan = _load_plan(manifest)
    manifest_path = _manifest_path(manifest, plan)
    if manifest_path is None or not manifest_path.is_file():
        raise UpgradeError("rollback requires a prepared write-ahead manifest")
    durable = _load_plan(_read(manifest_path))
    durable["manifest"] = str(manifest_path)
    if durable.get("state") == "rolled-back":
        return {"status": "already-rolled-back", "manifest": str(manifest_path), "idempotent": True}
    root = Path(str(durable["transaction_root"])).resolve()
    runner_is_default = runner is None
    effective_runner = _default_runner if runner is None else runner
    fence = Path(str(durable["fence"])).resolve()
    fence.parent.mkdir(parents=True, exist_ok=True)
    with fence.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        durable["state"] = "rolling-back"
        _record_history(durable, "rollback-start", state="rolling-back")
        _persist(manifest_path, durable)
        rollback = _auto_rollback(
            durable,
            runner=effective_runner,
            runner_is_default=runner_is_default,
            transaction_root=root,
            manifest_path=manifest_path,
        )
        durable["rollback"] = rollback
        durable["state"] = rollback["status"]
        _record_history(durable, "rollback-complete", state=durable["state"])
        _persist(manifest_path, durable)
        if rollback["status"] != "rolled-back":
            raise UpgradeError("manual rollback is blocked; prior deployment evidence was preserved")
        return {
            "status": "rolled-back",
            "manifest": str(manifest_path),
            "restored_sha256": rollback["artifact"].get("restored_sha256"),
            "rollback_phases": list(ROLLBACK_PHASE_ORDER),
        }


__all__ = [
    "ALL_PHASES",
    "PHASE_ORDER",
    "ROLLBACK_PHASE_ORDER",
    "UpgradeError",
    "apply_upgrade",
    "plan_upgrade",
    "prepare_upgrade",
    "rollback_upgrade",
]
