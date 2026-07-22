"""Ownership-manifest driven, recoverable install transaction.

The manifest is the only authority for filesystem ownership.  The optional
``InstallRuntime`` boundary adds the environment-specific writer/service
fence and post-install checks without giving the transaction permission to
inspect or mutate memory, ledgers, registries, shell startup files, launchers,
or credential stores.  Runtime commands are always tokenized and allowlisted;
the default subprocess adapter uses ``shell=False`` and a fixed minimal
environment.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux/WSL is the supported runtime.
    fcntl = None


class DeploymentError(ValueError):
    """Unsafe manifest or an inconsistent transaction."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


CommandRunner = Callable[..., Any]
GateCallback = Callable[..., Any]


# The phase names are part of the journal contract.  ``filesystem_apply`` is
# internal; the other phases are supplied by the environment-specific runner.
INSTALL_PHASE_ORDER = (
    "writer_lock",
    "stop_timer_service",
    "drain_writers",
    "filesystem_apply",
    "reinstall_hooks",
    "reinstall_service",
    "daemon_reload",
    "start_timer_service",
    "doctor_static",
    "profile_probe",
)
EXTERNAL_INSTALL_PHASES = (
    "stop_timer_service",
    "drain_writers",
    "reinstall_hooks",
    "reinstall_service",
    "daemon_reload",
    "start_timer_service",
)
ROLLBACK_PHASE_ORDER = (
    "rollback_start_timer_service",
    "rollback_daemon_reload",
    "rollback_reinstall_service",
    "rollback_reinstall_hooks",
    "release_writers",
    "rollback_stop_timer_service",
)

_KNOWN_COMMAND_PHASES = set(EXTERNAL_INSTALL_PHASES) | set(ROLLBACK_PHASE_ORDER)
_FAILURE_STATUSES = {"blocked", "failed", "failure", "error", "timeout"}
_SUCCESS_STATUSES = {"ok", "passed", "success", "completed", "drained", "reloaded"}
_SENSITIVE_ARG_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|password|secret|credential|oauth)"
)
_SHELL_META = re.compile(r"[\n\r;&|`$()]|\x00")
_FORBIDDEN_EXECUTABLES = {"sh", "bash", "zsh", "fish", "dash", "env", "sudo"}


@dataclass(frozen=True)
class InstallContext:
    """Safe context passed to doctor/profile callbacks.

    Callbacks receive paths as resolved values for target selection, but the
    transaction never serializes callback return data wholesale.  Journal
    evidence is reduced to an allowlisted status/result summary.
    """

    manifest_path: Path
    target_root: Path
    transaction_root: Path
    journal_path: Path
    profile_id: str
    phase: str


@dataclass(frozen=True)
class InstallRuntime:
    """Optional live deployment boundary for an install transaction.

    ``commands`` and ``rollback_commands`` are maps from fixed phase names to
    argv token lists.  Supplying a runtime opts into the fail-closed live
    contract: every external phase and its compensating command must be
    present, and both doctor/profile callbacks must return explicit success.
    """

    commands: Mapping[str, Sequence[str]]
    rollback_commands: Mapping[str, Sequence[str]]
    command_runner: CommandRunner
    rollback_runner: CommandRunner
    doctor_static: GateCallback
    profile_probe: GateCallback
    profile_id: str = "default"
    command_timeout: float = 60.0
    drain_timeout: float = 60.0
    lock_timeout: float = 30.0
    rollback_timeout: float = 60.0
    runner_location: str | Path | None = None
    rollback_runner_location: str | Path | None = None
    rollback_target_root: str | Path | None = None


class AllowlistedCommandRunner:
    """Tokenized command runner used by tests and release orchestration.

    The runner validates the phase-to-argv map at construction and validates
    the argv again at invocation.  Tests can provide ``executor`` to record
    calls; without one, the bounded subprocess adapter uses ``shell=False``.
    """

    def __init__(
        self,
        commands: Mapping[str, Sequence[str]],
        *,
        executor: CommandRunner | None = None,
        location: str | Path | None = None,
        target_root: str | Path | None = None,
    ) -> None:
        self.commands = _validate_command_map(commands)
        self.executor = executor
        self.location = Path(location).expanduser() if location is not None else None
        self.target_root = Path(target_root).expanduser() if target_root is not None else None

    def run(self, argv: Sequence[str], *, phase: str, **kwargs: Any) -> Any:
        expected = self.commands.get(phase)
        if expected is None:
            raise DeploymentError(f"command phase is not allowlisted: {phase}")
        if tuple(argv) != expected:
            raise DeploymentError(f"command argv drift for phase: {phase}")
        callback = self.executor or _subprocess_command_runner
        return _invoke_callable(callback, list(expected), phase=phase, **kwargs)


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
    "raw",
    "memory",
    "knowledge",
    "runtime/ledger",
    "runtime/" + "indexes",
    "runtime/recovery",
    "runtime/logs",
    "runtime/queue",
    "runtime/locks",
    "config/projects.yaml",
    "config/project-hippo.yaml",
)

_PROTECTED_WORDS = {
    "memory",
    "ledger",
    "ledgers",
    "registry",
    "project-registry",
    "knowledge",
    "archive",
    "inbox",
    "shell",
    "launcher",
    "credential",
    "credentials",
    "oauth",
    "secret",
    "secrets",
}


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_timeout(value: float, name: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise DeploymentError(f"{name} must be a number") from exc
    if not 0 < timeout <= 300:
        raise DeploymentError(f"{name} must be within (0, 300] seconds")
    return timeout


def _safe_command_token(token: object, *, phase: str) -> str:
    if not isinstance(token, str) or not token or _SHELL_META.search(token):
        raise DeploymentError(f"{phase}: command token is unsafe")
    if _SENSITIVE_ARG_RE.search(token):
        raise DeploymentError(f"{phase}: credential-shaped command token is forbidden")
    if token in {"-c", "-lc", "--command", "--shell"}:
        raise DeploymentError(f"{phase}: shell wrapper is forbidden")
    if "{" in token or "}" in token:
        raise DeploymentError(f"{phase}: command interpolation is forbidden")
    return token


def _validate_argv(argv: Sequence[str], *, phase: str) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise DeploymentError(f"{phase}: command must be a non-empty argv list")
    values = tuple(_safe_command_token(token, phase=phase) for token in argv)
    if Path(values[0]).name.casefold() in _FORBIDDEN_EXECUTABLES:
        raise DeploymentError(f"{phase}: shell wrapper executable is forbidden")
    return values


def _validate_command_map(commands: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    if not isinstance(commands, Mapping):
        raise DeploymentError("command allowlist must be an object")
    normalized: dict[str, tuple[str, ...]] = {}
    for phase, argv in commands.items():
        if phase not in _KNOWN_COMMAND_PHASES:
            raise DeploymentError(f"unknown command phase: {phase}")
        normalized[str(phase)] = _validate_argv(argv, phase=str(phase))
    return normalized


def _runner_location(value: object, *, role: str, target_root: Path, transaction_root: Path) -> str | None:
    if value is None:
        return None
    location = Path(str(value)).expanduser()
    if not location.is_absolute():
        raise DeploymentError(f"{role} location must be absolute")
    resolved = location.resolve(strict=False)
    if _path_is_within(resolved, target_root) or _path_is_within(resolved, transaction_root):
        raise DeploymentError(f"{role} location must be independent of target and transaction roots")
    if location.exists() and (not location.is_file() or not os.access(location, os.X_OK)):
        raise DeploymentError(f"{role} location must be an executable file")
    return str(resolved)


def _validate_runner_boundary(
    runner: object,
    *,
    role: str,
    target_root: Path,
    transaction_root: Path,
    declared_location: str | Path | None = None,
    declared_target: str | Path | None = None,
) -> dict[str, str | None]:
    location = declared_location
    if location is None:
        location = getattr(runner, "location", None)
    location_text = _runner_location(
        location, role=role, target_root=target_root, transaction_root=transaction_root
    )
    runner_target = declared_target
    if runner_target is None:
        runner_target = getattr(runner, "target_root", None)
    if runner_target is not None:
        resolved_target = Path(str(runner_target)).expanduser().resolve()
        if resolved_target != target_root.resolve():
            raise DeploymentError(f"{role} target root does not match install target")
    return {
        "role": role,
        "location": "external" if location_text else "injected",
        "location_sha256": _sha_bytes(location_text.encode("utf-8")) if location_text else None,
        "target_root": str(target_root),
    }


def _safe_result_details(result: object) -> dict[str, Any]:
    """Keep only bounded, non-secret result metadata in the journal."""

    if isinstance(result, subprocess.CompletedProcess):
        return {"returncode": int(result.returncode)}
    if isinstance(result, bool):
        return {"ok": result}
    if isinstance(result, int):
        return {"returncode": int(result)}
    if not isinstance(result, Mapping):
        return {"result_type": type(result).__name__}
    allowed = {
        "ok",
        "status",
        "returncode",
        "return_code",
        "drained",
        "locked",
        "daemon_reloaded",
        "hooks_reinstalled",
        "service_reinstalled",
        "service_state",
        "profile_id",
        "artifact_sha256",
        "build_commit",
    }
    details: dict[str, Any] = {}
    for key in allowed:
        if key not in result:
            continue
        value = result[key]
        if isinstance(value, (bool, int)):
            details[key] = value
        elif isinstance(value, str) and len(value) <= 128 and not _SENSITIVE_ARG_RE.search(value):
            details[key] = value
    return details


def _normalize_success(result: object, *, phase: str) -> tuple[bool, dict[str, Any], str | None]:
    details = _safe_result_details(result)
    if isinstance(result, bool):
        return result, details, None if result else f"{phase}: callback returned false"
    if isinstance(result, int):
        return result == 0, details, None if result == 0 else f"{phase}: returned {result}"
    if isinstance(result, subprocess.CompletedProcess):
        ok = int(result.returncode) == 0
        return ok, details, None if ok else f"{phase}: returned {result.returncode}"
    if isinstance(result, Mapping):
        status = str(result.get("status", "")).casefold()
        returncode = result.get("returncode", result.get("return_code"))
        if returncode is not None:
            try:
                returncode = int(returncode)
            except (TypeError, ValueError):
                return False, details, f"{phase}: invalid returncode"
            if returncode != 0:
                return False, details, f"{phase}: returned {returncode}"
        if result.get("ok") is False or status in _FAILURE_STATUSES:
            return False, details, f"{phase}: explicit failure"
        if result.get("ok") is True or status in _SUCCESS_STATUSES or returncode == 0:
            return True, details, None
    return False, details, f"{phase}: callback returned no explicit success"


def _invoke_callable(callback: CommandRunner | GateCallback, *args: Any, **kwargs: Any) -> Any:
    """Call injected functions while keeping simple test doubles ergonomic."""

    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*args, **kwargs)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return callback(*args, **kwargs)
    accepted = {name: value for name, value in kwargs.items() if name in parameters}
    positional = list(args)
    if not positional and parameters:
        first = next(iter(parameters.values()))
        if first.name in kwargs and first.name not in accepted:
            positional.append(kwargs[first.name])
    return callback(*positional, **accepted)


def _subprocess_command_runner(
    argv: Sequence[str],
    *,
    phase: str,
    timeout: float,
    cwd: str,
    env: Mapping[str, str],
    **_: Any,
) -> Mapping[str, Any]:
    if not argv:
        raise DeploymentError(f"{phase}: empty command")
    try:
        completed = subprocess.run(
            list(argv),
            shell=False,
            check=False,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env=dict(env),
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentError(f"{phase}: command timed out") from exc
    return {"returncode": int(completed.returncode)}


def _sanitize_reason(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|credential|oauth)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer <redacted>", text)
    return text[:256]


def _command_fingerprint(argv: Sequence[str]) -> str:
    return _sha_bytes("\0".join(argv).encode("utf-8"))


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


def _assert_no_symlink_components(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DeploymentError(f"manifest target must not traverse a symlink: {relative.as_posix()}")


def _validate_target_root(value: str | Path) -> Path:
    root = Path(value).expanduser()
    resolved = root.resolve()
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise DeploymentError("target root is too broad")
    if root.exists() and root.is_symlink():
        raise DeploymentError("target root must not be a symlink")
    return resolved


def _validate_transaction_root(value: str | Path, *, target_root: Path) -> Path:
    root = Path(value).expanduser()
    resolved = root.resolve()
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise DeploymentError("transaction root is too broad")
    if _path_is_within(resolved, target_root) or _path_is_within(target_root, resolved):
        raise DeploymentError("transaction root must be independent of target root")
    if root.exists() and root.is_symlink():
        raise DeploymentError("transaction root must not be a symlink")
    return resolved


def _is_protected(relative: Path) -> bool:
    # Keep a leading dot: `.config` and `.local` are protected roots too.
    text = relative.as_posix().lstrip("/")
    if text in {".hippo-install-state.json", ".hippo-install.lock"} or text.startswith(".hippo-install-"):
        return True
    if any(text == prefix or text.startswith(prefix + "/") for prefix in PROTECTED_PREFIXES):
        return True
    parts = {part.casefold() for part in relative.parts}
    return bool(parts & _PROTECTED_WORDS) or any("launcher" in part for part in parts)


def _target_path(target_root: Path, relative: Path) -> Path:
    root = target_root.resolve()
    _assert_no_symlink_components(root, relative)
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


def _validate_runtime(runtime: InstallRuntime, *, target_root: Path, transaction_root: Path) -> dict[str, Any]:
    if not isinstance(runtime, InstallRuntime):
        raise DeploymentError("runtime must be an InstallRuntime")
    if not runtime.profile_id or _SENSITIVE_ARG_RE.search(runtime.profile_id):
        raise DeploymentError("runtime profile_id is invalid")
    command_timeout = _validate_timeout(runtime.command_timeout, "command_timeout")
    drain_timeout = _validate_timeout(runtime.drain_timeout, "drain_timeout")
    lock_timeout = _validate_timeout(runtime.lock_timeout, "lock_timeout")
    rollback_timeout = _validate_timeout(runtime.rollback_timeout, "rollback_timeout")
    commands = _validate_command_map(runtime.commands)
    rollback_commands = _validate_command_map(runtime.rollback_commands)
    missing = [phase for phase in EXTERNAL_INSTALL_PHASES if phase not in commands]
    missing += [phase for phase in ROLLBACK_PHASE_ORDER if phase not in rollback_commands]
    if missing:
        raise DeploymentError("runtime command allowlist is incomplete: " + ", ".join(missing))
    if not callable(runtime.command_runner) and not callable(getattr(runtime.command_runner, "run", None)):
        raise DeploymentError("runtime command_runner is not callable")
    if not callable(runtime.rollback_runner) and not callable(getattr(runtime.rollback_runner, "run", None)):
        raise DeploymentError("runtime rollback_runner is not callable")
    if not callable(runtime.doctor_static) or not callable(runtime.profile_probe):
        raise DeploymentError("runtime doctor_static and profile_probe callbacks are required")
    command_boundary = _validate_runner_boundary(
        runtime.command_runner,
        role="command runner",
        target_root=target_root,
        transaction_root=transaction_root,
        declared_location=runtime.runner_location,
    )
    rollback_boundary = _validate_runner_boundary(
        runtime.rollback_runner,
        role="rollback runner",
        target_root=target_root,
        transaction_root=transaction_root,
        declared_location=runtime.rollback_runner_location,
        declared_target=runtime.rollback_target_root,
    )
    return {
        "commands": commands,
        "rollback_commands": rollback_commands,
        "command_timeout": command_timeout,
        "drain_timeout": drain_timeout,
        "lock_timeout": lock_timeout,
        "rollback_timeout": rollback_timeout,
        "command_boundary": command_boundary,
        "rollback_boundary": rollback_boundary,
    }


def _minimal_env(*, phase: str, profile_id: str) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "LC_ALL": "C",
        "HIPPO_INSTALL_PHASE": phase,
        "HIPPO_PROFILE_ID": profile_id,
    }


def _run_command_phase(
    runtime: InstallRuntime,
    runtime_info: Mapping[str, Any],
    *,
    phase: str,
    context: InstallContext,
) -> dict[str, Any]:
    argv = runtime_info["commands"][phase]
    timeout = runtime_info["drain_timeout"] if phase == "drain_writers" else runtime_info["command_timeout"]
    callback = getattr(runtime.command_runner, "run", runtime.command_runner)
    result = _invoke_callable(
        callback,
        list(argv),
        phase=phase,
        profile_id=context.profile_id,
        timeout=timeout,
        cwd=str(context.transaction_root),
        env=_minimal_env(phase=phase, profile_id=context.profile_id),
        target_root=str(context.target_root),
        transaction_root=str(context.transaction_root),
    )
    ok, details, reason = _normalize_success(result, phase=phase)
    if not ok:
        raise DeploymentError(reason or f"{phase}: command failed")
    details["argv_sha256"] = _command_fingerprint(argv)
    return details


def _invoke_gate(callback: GateCallback, context: InstallContext, *, profile_id: str | None = None) -> Any:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(context, profile_id) if profile_id is not None else callback(context)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values()):
        return callback(context, profile_id) if profile_id is not None else callback(context)
    if profile_id is not None and len(positional) >= 2:
        return callback(context, profile_id)
    if positional:
        return callback(context)
    kwargs = {}
    if "context" in signature.parameters:
        kwargs["context"] = context
    if "profile_id" in signature.parameters and profile_id is not None:
        kwargs["profile_id"] = profile_id
    return callback(**kwargs)


def _gate_phase(
    callback: GateCallback,
    context: InstallContext,
    *,
    phase: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    result = _invoke_gate(callback, context, profile_id=profile_id)
    ok, details, reason = _normalize_success(result, phase=phase)
    if not ok:
        raise DeploymentError(reason or f"{phase}: gate failed")
    if profile_id is not None and details.get("profile_id") not in {None, profile_id}:
        raise DeploymentError(f"{phase}: profile identity mismatch")
    return details


@contextmanager
def _bounded_lock(path: Path, *, timeout: float) -> Iterator[None]:
    """Acquire a process/shared-host install fence without unbounded wait."""

    if fcntl is None:  # pragma: no cover - supported hosts have fcntl.
        raise DeploymentError("writer lock is unavailable on this platform")
    if path.is_symlink():
        raise DeploymentError("writer lock path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DeploymentError("writer lock acquisition timed out")
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _entry_plan(entry: Mapping[str, Any], target_root: Path, package_root: Path, previous: Mapping[str, Any]) -> dict[str, Any]:
    relative, path = _entry_path(entry, target_root)
    kind = str(entry.get("kind", "exclusive"))
    if kind not in {"exclusive", "shared-json"}:
        raise DeploymentError(f"unsupported ownership kind: {kind}")
    if kind == "shared-json":
        desired_owned = _shared_desired(entry)
        current = _read_shared(path)
        previous_owned = previous.get("owned_entries", {}) if isinstance(previous, Mapping) else {}
        previous_hash = previous.get("sha256") if isinstance(previous, Mapping) else None
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
            "previous_hash": previous_hash,
            "hash_drift": bool(previous_hash and previous_hash != _sha_bytes(_json_bytes(current))),
            "current_owned_entries": {
                str(key): {"present": key in current, "value": current.get(key)}
                for key in desired_owned
            },
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
    target = _validate_target_root(target_root)
    package = Path(package_root).expanduser() if package_root else manifest_file.parent
    # State belongs to the target surface, not beside the package manifest.
    # The latter may be inside a read-only wheel/site-packages directory and
    # must never become an implicit write target.
    state_file = Path(state_path).expanduser().resolve() if state_path else target / ".hippo-install-state.json"
    if not _path_is_within(state_file, target):
        raise DeploymentError("install state must remain below target root")
    if state_file.is_symlink():
        raise DeploymentError("install state must not be a symlink")
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
            conflicts = [key for key, value in owned.items() if key in current and current[key] != value]
            removable = {key: value for key, value in owned.items() if current.get(key, object()) == value}
            rows.append({
                "path": path_text,
                "kind": "shared-json",
                "action": "shared-remove" if removable else "keep",
                "current_hash": _sha_bytes(_json_bytes(current)) if target_path.exists() else None,
                "previous_hash": old.get("sha256"),
                "hash_drift": bool(old.get("sha256") and old.get("sha256") != _sha_bytes(_json_bytes(current))),
                "current_owned_entries": {
                    str(key): {"present": key in current, "value": current.get(key)} for key in owned
                },
                "owned_entries": dict(owned),
                "remove_entries": removable,
                "previous_owned_entries": dict(owned),
                "conflicts": conflicts,
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
    state_required = not state_file.exists()
    return {
        "schema_version": "1",
        "manifest": str(manifest_file),
        "target_root": str(target.resolve()),
        "state_path": str(state_file),
        "force_required": state_required or any(
            row["action"] in {"create", "update", "remove", "shared-remove"} for row in rows
        ),
        "state_required": state_required,
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
    if path.is_symlink():
        raise DeploymentError(f"refusing to replace symlink: {path.name}")
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
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"exclusive owned path is not a regular file: {relative}")
    relative_path = _safe_relative(relative)
    if _is_protected(relative_path):
        raise DeploymentError(f"protected path cannot be backed up: {relative}")
    destination = backup_root / _safe_relative(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(destination, path.read_bytes())
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return str(destination)


def _persist_journal(path: Path, journal: Mapping[str, Any]) -> None:
    _atomic_write(path, _json_bytes(journal))


def _phase_start(journal: dict[str, Any], journal_path: Path, phase: str, *, command_sha256: str | None = None) -> None:
    evidence = journal["phases"][phase]
    evidence["status"] = "running"
    evidence["attempts"] = int(evidence.get("attempts", 0)) + 1
    evidence["started_at"] = _utc_now()
    if command_sha256:
        evidence["argv_sha256"] = command_sha256
    journal.setdefault("history", []).append({"event": "phase-start", "phase": phase, "at": evidence["started_at"]})
    _persist_journal(journal_path, journal)


def _phase_pass(journal: dict[str, Any], journal_path: Path, phase: str, details: Mapping[str, Any] | None = None) -> None:
    evidence = journal["phases"][phase]
    evidence["status"] = "passed"
    evidence["finished_at"] = _utc_now()
    if details:
        evidence["result"] = dict(details)
    journal.setdefault("history", []).append({"event": "phase-pass", "phase": phase, "at": evidence["finished_at"]})
    _persist_journal(journal_path, journal)


def _phase_fail(journal: dict[str, Any], journal_path: Path, phase: str, reason: object) -> None:
    evidence = journal["phases"][phase]
    evidence["status"] = "failed"
    evidence["finished_at"] = _utc_now()
    evidence["failure"] = {"reason": _sanitize_reason(reason)}
    journal.setdefault("history", []).append({"event": "phase-fail", "phase": phase, "at": evidence["finished_at"]})
    _persist_journal(journal_path, journal)


def _state_payload(manifest: Mapping[str, Any], plan: Mapping[str, Any], target: Path) -> dict[str, Any]:
    plan_rows = {str(row["path"]): row for row in plan.get("entries", [])}
    state_entries: dict[str, Any] = {}
    for entry in _load_entries(manifest):
        path_text = str(entry["path"])
        row = plan_rows[path_text]
        destination = _target_path(target, _safe_relative(path_text))
        if entry.get("kind", "exclusive") == "shared-json":
            owned = _shared_desired(entry)
            sha256 = _sha_bytes(_json_bytes(_read_shared(destination))) if destination.exists() else None
            state_entries[path_text] = {"kind": "shared-json", "owned_entries": owned, "sha256": sha256}
        else:
            sha256 = _sha_bytes(destination.read_bytes()) if destination.is_file() else row.get("desired_hash")
            state_entries[path_text] = {"kind": "exclusive", "sha256": sha256}
    return {"schema_version": "1", "entries": state_entries}


def _owned_snapshot(current: Mapping[str, Any], keys: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): {"present": key in current, "value": current.get(key)}
        for key in keys
    }


def _check_plan_drift(row: Mapping[str, Any], path: Path, *, kind: str, owned_keys: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if kind == "shared-json":
        current = _read_shared(path)
        expected = row.get("current_owned_entries", {})
        actual = _owned_snapshot(current, owned_keys or {})
        if isinstance(expected, Mapping) and dict(expected) != actual:
            raise DeploymentError(f"ownership drift during apply: {row['path']}")
        return current
    current_bytes = path.read_bytes() if path.is_file() else None
    expected_hash = row.get("current_hash")
    actual_hash = _sha_bytes(current_bytes) if current_bytes is not None else None
    if actual_hash != expected_hash:
        raise DeploymentError(f"ownership drift during apply: {row['path']}")
    return None


def _apply_filesystem(
    journal: dict[str, Any],
    journal_path: Path,
    *,
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
    package: Path,
    target: Path,
    transaction_root: Path,
) -> list[dict[str, Any]]:
    entries = {str(entry["path"]): entry for entry in _load_entries(manifest)}
    applied: list[dict[str, Any]] = []
    for row in plan.get("entries", []):
        action = str(row.get("action", "keep"))
        if action == "keep":
            continue
        relative = _safe_relative(str(row["path"]))
        path = _target_path(target, relative)
        kind = str(row.get("kind", "exclusive"))
        entry = entries.get(str(row["path"]))
        backup = None
        shared_before: dict[str, Any] | None = None
        shared_after: dict[str, Any] | None = None
        shared_file_before = path.is_file()
        pre_hash = _sha_bytes(_json_bytes(_read_shared(path))) if kind == "shared-json" and path.exists() else (
            _sha_bytes(path.read_bytes()) if path.is_file() else None
        )
        if kind == "shared-json":
            owned_values = _shared_desired(entry) if entry is not None else dict(row.get("remove_entries", {}))
            current = _check_plan_drift(row, path, kind=kind, owned_keys=owned_values) or {}
            shared_before = _owned_snapshot(current, owned_values)
            if entry is not None:
                for key, value in owned_values.items():
                    current[key] = value
            else:
                for key, value in owned_values.items():
                    if current.get(key, object()) == value:
                        current.pop(key, None)
            shared_after = _owned_snapshot(current, owned_values)
            if current or path.exists():
                _atomic_write(path, _json_bytes(current))
            post_hash = _sha_bytes(_json_bytes(current)) if path.exists() else None
        else:
            _check_plan_drift(row, path, kind=kind)
            if action in {"update", "remove"}:
                backup = _backup(path, transaction_root / "preimage", str(row["path"]))
            if action in {"create", "update"}:
                if entry is None:
                    raise DeploymentError(f"manifest entry disappeared: {row['path']}")
                _atomic_write(path, _entry_bytes(entry, package))
            elif action == "remove" and path.exists():
                path.unlink()
                _fsync_parent(path)
            post_hash = _sha_bytes(path.read_bytes()) if path.is_file() else None
        record = {
            "path": str(row["path"]),
            "kind": kind,
            "action": action,
            "backup": backup,
            "pre_hash": pre_hash,
            "post_hash": post_hash,
            "shared_before": shared_before,
            "shared_after": shared_after,
            "shared_file_before": shared_file_before,
        }
        applied.append(record)
        journal["applied"] = applied
        journal["mutation_count"] = len(applied)
        _persist_journal(journal_path, journal)
    state_path = Path(str(plan["state_path"])).resolve()
    state_payload = _state_payload(manifest, plan, target)
    _atomic_write(state_path, _json_bytes(state_payload))
    journal["state_after_hash"] = _sha_bytes(state_path.read_bytes())
    journal["state_written"] = True
    journal["applied"] = applied
    _persist_journal(journal_path, journal)
    return applied


def _rollback_filesystem(journal: dict[str, Any], journal_path: Path, *, target: Path) -> dict[str, Any]:
    applied = journal.get("applied", [])
    if not isinstance(applied, list):
        raise DeploymentError("transaction applied evidence is invalid")
    conflicts: list[str] = []
    for row in applied:
        if not isinstance(row, Mapping):
            raise DeploymentError("transaction mutation evidence is invalid")
        path = _target_path(target, _safe_relative(str(row["path"])))
        if row.get("kind") == "shared-json" and isinstance(row.get("shared_after"), Mapping):
            current = _read_shared(path)
            for key, expected in row["shared_after"].items():
                actual = {"present": key in current, "value": current.get(key)}
                if actual != expected:
                    conflicts.append(f"{row['path']}:{key}")
            continue
        expected_hash = row.get("post_hash")
        current_hash = _sha_bytes(path.read_bytes()) if path.is_file() else None
        if current_hash != expected_hash:
            conflicts.append(str(row["path"]))
    state_path_text = journal.get("state_path")
    state_path = Path(str(state_path_text)).expanduser() if state_path_text else None
    expected_state_hash = journal.get("state_after_hash")
    if state_path is not None and expected_state_hash:
        current_state_hash = _sha_bytes(state_path.read_bytes()) if state_path.is_file() else None
        if current_state_hash != expected_state_hash:
            conflicts.append(".hippo-install-state.json")
    if conflicts:
        result = {"status": "rollback-blocked", "conflicts": sorted(set(conflicts)), "restored": []}
        journal["filesystem_rollback"] = result
        _persist_journal(journal_path, journal)
        return result
    restored: list[str] = []
    for row in applied:
        path = _target_path(target, _safe_relative(str(row["path"])))
        if row.get("kind") == "shared-json" and isinstance(row.get("shared_before"), Mapping):
            current = _read_shared(path)
            for key, before in row["shared_before"].items():
                if before.get("present"):
                    current[key] = before.get("value")
                else:
                    current.pop(key, None)
            if current or row.get("shared_file_before"):
                _atomic_write(path, _json_bytes(current))
            elif path.exists():
                path.unlink()
                _fsync_parent(path)
            restored.append(str(row["path"]))
            continue
        backup = row.get("backup")
        if backup:
            backup_path = Path(str(backup)).resolve()
            if not backup_path.is_file() or backup_path.is_symlink():
                raise DeploymentError("exclusive backup is unavailable")
            _atomic_write(path, backup_path.read_bytes())
            restored.append(str(row["path"]))
        elif row.get("action") == "create" and path.exists():
            path.unlink()
            _fsync_parent(path)
            restored.append(str(row["path"]))
    state_backup = journal.get("state_backup")
    if state_path is not None:
        if state_backup and Path(str(state_backup)).is_file():
            _atomic_write(state_path, Path(str(state_backup)).read_bytes())
        elif not journal.get("state_existed", False) and state_path.exists():
            state_path.unlink()
            _fsync_parent(state_path)
    result = {"status": "rolled-back", "conflicts": [], "restored": restored}
    journal["filesystem_rollback"] = result
    _persist_journal(journal_path, journal)
    return result


def _run_external_rollback(
    journal: dict[str, Any],
    journal_path: Path,
    *,
    runtime: InstallRuntime | None,
    runtime_info: Mapping[str, Any] | None,
    target: Path,
    transaction_root: Path,
) -> dict[str, Any]:
    if runtime is None or runtime_info is None:
        result = {"status": "not-requested", "phases": {}}
        journal["external_rollback"] = result
        _persist_journal(journal_path, journal)
        return result
    phase_status = journal.get("phases", {})
    results: dict[str, Any] = {}
    rollback_map = {
        "rollback_start_timer_service": "start_timer_service",
        "rollback_daemon_reload": "daemon_reload",
        "rollback_reinstall_service": "reinstall_service",
        "rollback_reinstall_hooks": "reinstall_hooks",
        "release_writers": "drain_writers",
        "rollback_stop_timer_service": "stop_timer_service",
    }
    for rollback_phase in ROLLBACK_PHASE_ORDER:
        source_phase = rollback_map[rollback_phase]
        source_status = phase_status.get(source_phase, {}).get("status")
        if source_status in {None, "pending", "skipped"}:
            journal["rollback_phases"][rollback_phase]["status"] = "skipped"
            journal["rollback_phases"][rollback_phase]["reason"] = "source phase not entered"
            _persist_journal(journal_path, journal)
            continue
        evidence = journal["rollback_phases"][rollback_phase]
        evidence["status"] = "running"
        evidence["attempts"] = int(evidence.get("attempts", 0)) + 1
        evidence["started_at"] = _utc_now()
        evidence["argv_sha256"] = _command_fingerprint(runtime_info["rollback_commands"][rollback_phase])
        _persist_journal(journal_path, journal)
        context = InstallContext(
            manifest_path=Path(str(journal["manifest_path"])),
            target_root=target,
            transaction_root=transaction_root,
            journal_path=journal_path,
            profile_id=runtime.profile_id,
            phase=rollback_phase,
        )
        callback = getattr(runtime.rollback_runner, "run", runtime.rollback_runner)
        try:
            result = _invoke_callable(
                callback,
                list(runtime_info["rollback_commands"][rollback_phase]),
                phase=rollback_phase,
                profile_id=context.profile_id,
                timeout=runtime_info["rollback_timeout"],
                cwd=str(transaction_root),
                env=_minimal_env(phase=rollback_phase, profile_id=context.profile_id),
                target_root=str(target),
                transaction_root=str(transaction_root),
            )
            ok, details, reason = _normalize_success(result, phase=rollback_phase)
            if not ok:
                raise DeploymentError(reason or f"{rollback_phase}: rollback failed")
            details["argv_sha256"] = evidence["argv_sha256"]
            evidence["status"] = "passed"
            evidence["finished_at"] = _utc_now()
            evidence["result"] = details
            results[rollback_phase] = details
        except Exception as exc:
            evidence["status"] = "failed"
            evidence["finished_at"] = _utc_now()
            evidence["failure"] = {"reason": _sanitize_reason(exc)}
            results[rollback_phase] = {"status": "blocked", "reason": _sanitize_reason(exc)}
        _persist_journal(journal_path, journal)
    blocked = [name for name, value in results.items() if value.get("status") == "blocked"]
    result = {"status": "rollback-blocked" if blocked else "rolled-back", "phases": results}
    journal["external_rollback"] = result
    _persist_journal(journal_path, journal)
    return result


def _auto_rollback(
    journal: dict[str, Any],
    journal_path: Path,
    *,
    runtime: InstallRuntime | None,
    runtime_info: Mapping[str, Any] | None,
    target: Path,
    transaction_root: Path,
) -> dict[str, Any]:
    journal["state"] = "rollback-pending"
    journal["rollback_started_at"] = _utc_now()
    _persist_journal(journal_path, journal)
    try:
        filesystem = _rollback_filesystem(journal, journal_path, target=target)
    except Exception as exc:
        filesystem = {"status": "rollback-blocked", "reason": _sanitize_reason(exc), "restored": []}
        journal["filesystem_rollback"] = filesystem
        _persist_journal(journal_path, journal)
    external = _run_external_rollback(
        journal,
        journal_path,
        runtime=runtime,
        runtime_info=runtime_info,
        target=target,
        transaction_root=transaction_root,
    )
    status = "rolled-back" if filesystem.get("status") == "rolled-back" and external.get("status") in {"rolled-back", "not-requested"} else "rollback-blocked"
    result = {"status": status, "filesystem": filesystem, "external": external, "finished_at": _utc_now()}
    journal["rollback"] = result
    journal["state"] = status
    journal["rollback_finished_at"] = result["finished_at"]
    _persist_journal(journal_path, journal)
    return result


def _new_journal(
    *,
    plan: Mapping[str, Any],
    manifest_path: Path,
    target: Path,
    transaction_root: Path,
    state_path: Path,
    state_existed: bool,
    state_backup: Path,
    runtime: InstallRuntime | None,
    runtime_info: Mapping[str, Any] | None,
) -> dict[str, Any]:
    phases = {phase: {"status": "pending", "attempts": 0} for phase in INSTALL_PHASE_ORDER}
    rollback_phases = {phase: {"status": "pending", "attempts": 0} for phase in ROLLBACK_PHASE_ORDER}
    if runtime is None:
        for phase in EXTERNAL_INSTALL_PHASES + ("doctor_static", "profile_probe"):
            phases[phase] = {"status": "skipped", "reason": "runtime-not-configured", "attempts": 0}
    return {
        "schema_version": "2",
        "state": "prepared",
        "token": secrets.token_hex(8),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha_bytes(manifest_path.read_bytes()),
        "target_root": str(target),
        "transaction_root": str(transaction_root),
        "plan": dict(plan),
        "state_path": str(state_path),
        "state_existed": state_existed,
        "state_backup": str(state_backup) if state_existed else None,
        "phases": phases,
        "rollback_phases": rollback_phases,
        "phase_order": list(INSTALL_PHASE_ORDER),
        "rollback_phase_order": list(ROLLBACK_PHASE_ORDER),
        "runtime": {
            "enabled": runtime is not None,
            "profile_id": runtime.profile_id if runtime is not None else None,
            "command_runner": runtime_info.get("command_boundary") if runtime_info else None,
            "rollback_runner": runtime_info.get("rollback_boundary") if runtime_info else None,
        },
        "applied": [],
        "history": [],
    }


def apply_install(
    plan: Mapping[str, Any],
    *,
    manifest_path: str | Path,
    package_root: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    transaction_root: str | Path | None = None,
    runtime: InstallRuntime | None = None,
) -> dict[str, Any]:
    if plan.get("conflicts"):
        raise DeploymentError("ownership conflict requires operator review")
    if not force and plan.get("force_required"):
        raise DeploymentError("--force is required for ownership changes")
    target = _validate_target_root(str(plan["target_root"]))
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = _read_json(manifest_file)
    package = Path(package_root).expanduser().resolve() if package_root else manifest_file.parent
    if dry_run:
        dry_root = _validate_transaction_root(
            transaction_root or (Path(tempfile.gettempdir()) / "hippo-install-dry-run"),
            target_root=target,
        )
        if runtime is not None:
            _validate_runtime(runtime, target_root=target, transaction_root=dry_root)
        return {
            "status": "dry-run",
            "mutation": False,
            "phase_order": list(INSTALL_PHASE_ORDER),
            "rollback_phase_order": list(ROLLBACK_PHASE_ORDER),
            "plan": dict(plan),
        }
    if not plan.get("force_required") and Path(str(plan["state_path"])).is_file():
        return {
            "status": "applied",
            "idempotent": True,
            "mutation": False,
            "transaction": None,
            "applied": [],
        }
    token = secrets.token_hex(8)
    requested_tx = transaction_root or (Path(tempfile.gettempdir()) / f"hippo-install-{token}")
    tx_root = _validate_transaction_root(requested_tx, target_root=target)
    tx_root.mkdir(parents=True, exist_ok=True)
    journal = tx_root / "transaction.json"
    if journal.is_symlink():
        raise DeploymentError("transaction journal must not be a symlink")
    state_path = Path(str(plan["state_path"])).expanduser().resolve()
    if not _path_is_within(state_path, target):
        raise DeploymentError("install state must remain below target root")
    state_existed = state_path.is_file()
    if state_path.exists() and (state_path.is_symlink() or not state_path.is_file()):
        raise DeploymentError("install state must be a regular file")
    state_backup = tx_root / "state-before.json"
    if state_existed:
        _atomic_write(state_backup, state_path.read_bytes())
        try:
            os.chmod(state_backup, 0o600)
        except OSError:
            pass
    runtime_info = _validate_runtime(runtime, target_root=target, transaction_root=tx_root) if runtime else None
    journal_payload = _new_journal(
        plan=plan,
        manifest_path=manifest_file,
        target=target,
        transaction_root=tx_root,
        state_path=state_path,
        state_existed=state_existed,
        state_backup=state_backup,
        runtime=runtime,
        runtime_info=runtime_info,
    )
    _persist_journal(journal, journal_payload)
    lock_timeout = runtime_info["lock_timeout"] if runtime_info else 30.0
    lock_path = tx_root / "writer.lock"
    try:
        with _bounded_lock(lock_path, timeout=lock_timeout):
            _phase_start(journal_payload, journal, "writer_lock")
            _phase_pass(journal_payload, journal, "writer_lock", {"locked": True})
            try:
                for phase in INSTALL_PHASE_ORDER:
                    if phase == "writer_lock":
                        continue
                    if journal_payload["phases"][phase].get("status") == "skipped":
                        continue
                    command_sha = None
                    if runtime_info and phase in EXTERNAL_INSTALL_PHASES:
                        command_sha = _command_fingerprint(runtime_info["commands"][phase])
                    _phase_start(journal_payload, journal, phase, command_sha256=command_sha)
                    context = InstallContext(
                        manifest_path=manifest_file,
                        target_root=target,
                        transaction_root=tx_root,
                        journal_path=journal,
                        profile_id=runtime.profile_id if runtime else "default",
                        phase=phase,
                    )
                    if phase == "filesystem_apply":
                        applied = _apply_filesystem(
                            journal_payload,
                            journal,
                            plan=plan,
                            manifest=manifest,
                            package=package,
                            target=target,
                            transaction_root=tx_root,
                        )
                        _phase_pass(journal_payload, journal, phase, {"mutation_count": len(applied)})
                    elif runtime is not None and phase in EXTERNAL_INSTALL_PHASES:
                        details = _run_command_phase(runtime, runtime_info, phase=phase, context=context)
                        _phase_pass(journal_payload, journal, phase, details)
                    elif runtime is not None and phase == "doctor_static":
                        details = _gate_phase(runtime.doctor_static, context, phase=phase)
                        _phase_pass(journal_payload, journal, phase, details)
                    elif runtime is not None and phase == "profile_probe":
                        details = _gate_phase(runtime.profile_probe, context, phase=phase, profile_id=runtime.profile_id)
                        _phase_pass(journal_payload, journal, phase, details)
                    else:
                        raise DeploymentError(f"unsupported install phase: {phase}")
                journal_payload["state"] = "committed"
                journal_payload["committed_at"] = _utc_now()
                _persist_journal(journal, journal_payload)
                return {
                    "status": "applied",
                    "idempotent": False,
                    "mutation": True,
                    "transaction": str(journal),
                    "applied": list(journal_payload.get("applied", [])),
                    "phase_order": list(INSTALL_PHASE_ORDER),
                }
            except Exception as exc:
                failed_phase = next(
                    (
                        phase
                        for phase, evidence in journal_payload["phases"].items()
                        if evidence.get("status") == "running"
                    ),
                    "unknown",
                )
                if failed_phase in journal_payload["phases"]:
                    _phase_fail(journal_payload, journal, failed_phase, exc)
                journal_payload["failure"] = {"phase": failed_phase, "reason": _sanitize_reason(exc), "at": _utc_now()}
                _persist_journal(journal, journal_payload)
                rollback = _auto_rollback(
                    journal_payload,
                    journal,
                    runtime=runtime,
                    runtime_info=runtime_info,
                    target=target,
                    transaction_root=tx_root,
                )
                raise DeploymentError(
                    f"install phase {failed_phase} failed; automatic {rollback['status']}"
                ) from exc
    except DeploymentError:
        raise
    except Exception as exc:
        raise DeploymentError("install transaction failed before apply") from exc


def rollback_install(
    value: Mapping[str, Any] | str | Path,
    *,
    runtime: InstallRuntime | None = None,
) -> dict[str, Any]:
    journal_path = Path(value["journal"] if isinstance(value, Mapping) else value).expanduser().resolve()
    if journal_path.is_symlink() or not journal_path.is_file():
        raise DeploymentError("rollback journal is unavailable")
    journal = _read_json(journal_path)
    target_value = journal.get("target_root") or journal.get("plan", {}).get("target_root")
    if not isinstance(target_value, str) or not target_value:
        raise DeploymentError("rollback journal has no target root")
    target = _validate_target_root(target_value)
    transaction_root = _validate_transaction_root(journal_path.parent, target_root=target)
    if journal.get("state") == "rolled-back":
        return {"status": "already-rolled-back", "idempotent": True, "journal": str(journal_path)}
    runtime_enabled = bool(journal.get("runtime", {}).get("enabled"))
    if runtime_enabled and runtime is None:
        raise DeploymentError("rollback requires the external rollback runtime")
    runtime_info = _validate_runtime(runtime, target_root=target, transaction_root=transaction_root) if runtime else None
    lock_timeout = runtime_info["lock_timeout"] if runtime_info else 30.0
    with _bounded_lock(transaction_root / "writer.lock", timeout=lock_timeout):
        result = _auto_rollback(
            journal,
            journal_path,
            runtime=runtime,
            runtime_info=runtime_info,
            target=target,
            transaction_root=transaction_root,
        )
    if result["status"] != "rolled-back":
        filesystem = result.get("filesystem", {})
        return {
            "status": "rollback-blocked",
            "journal": str(journal_path),
            "conflicts": list(filesystem.get("conflicts", [])),
            "rollback": result,
        }
    return {
        "status": "rolled-back",
        "journal": str(journal_path),
        "restored": result["filesystem"].get("restored", []),
        "rollback": result,
    }


def install_all(
    *,
    manifest_path: str | Path,
    target_root: str | Path,
    package_root: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    transaction_root: str | Path | None = None,
    runtime: InstallRuntime | None = None,
) -> dict[str, Any]:
    plan = plan_install(
        manifest_path,
        target_root=target_root,
        package_root=package_root,
    )
    return apply_install(
        plan,
        manifest_path=manifest_path,
        package_root=package_root,
        force=force,
        dry_run=dry_run,
        transaction_root=transaction_root,
        runtime=runtime,
    )


__all__ = [
    "AllowlistedCommandRunner",
    "DeploymentError",
    "EXTERNAL_INSTALL_PHASES",
    "INSTALL_PHASE_ORDER",
    "InstallContext",
    "InstallRuntime",
    "PROTECTED_PREFIXES",
    "ROLLBACK_PHASE_ORDER",
    "apply_install",
    "install_all",
    "plan_install",
    "rollback_install",
]
