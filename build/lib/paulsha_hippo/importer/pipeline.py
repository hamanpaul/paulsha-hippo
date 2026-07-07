"""Idempotent Stage 2 memory ingestion pipeline."""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .adapters import claude, codex, copilot
from .adapters.base import AdapterResult, NormalizedSession
from . import _git
from . import title
from .classifier import classify_session
from .frontmatter import render_markdown
from .project_resolver import normalize_remote
from .project_resolver import resolve_project

_SCOPE_RANK = {"turn": 0, "subagent": 0, "pre_compact": 0, "session_end": 1, "watcher_final": 2}
_TERMINAL_STATUSES = {"written", "updated", "hash-duplicate", "stale-skip"}
_LEDGER_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()


class PipelineError(Exception):
    """Raised for ingestion errors that callers should present cleanly."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def content_hash(session: NormalizedSession, capture_scope: str) -> str:
    subset = (
        session["session_id"],
        capture_scope,
        session["turn_count"],
        session["ended_at"],
        sorted(session["touched_files"]),
        len(session["user_prompts"]),
    )
    return sha256(_canonical_json(subset).encode("utf-8")).hexdigest()


def completeness(session: NormalizedSession, capture_scope: str) -> tuple[int, int, int, int]:
    return (
        _SCOPE_RANK.get(capture_scope, 0),
        session["turn_count"],
        len(session["touched_files"]),
        len(session["user_prompts"]),
    )


def _read_tool(queue_path: Path) -> str:
    try:
        with queue_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise PipelineError(f"queue item not found: {queue_path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"queue item is not valid JSON: {queue_path}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"queue item must contain a top-level JSON object: {queue_path}")
    tool = payload.get("tool") or payload.get("source_agent") or payload.get("agent")
    if isinstance(tool, str) and tool:
        return tool
    raise PipelineError(f"queue item is missing tool: {queue_path}")


def _extract(queue_path: Path) -> AdapterResult:
    tool = _read_tool(queue_path)
    normalized = tool.lower().replace("_", "-")
    if normalized in {"claude", "claude-code"}:
        return claude.extract(queue_path)
    if normalized == "codex":
        return codex.extract(queue_path)
    if normalized in {"copilot", "copilot-cli", "github-copilot-cli"}:
        return copilot.extract(queue_path)
    raise PipelineError(f"unsupported tool: {tool}")


def idempotency_key(session: NormalizedSession) -> str:
    return f"{session['tool']}:{session['session_id']}"


def safe_key(key: str) -> str:
    return re.sub(r"[\\/]+", "__", key.replace(":", "__"))


def _date_parts(session: NormalizedSession) -> tuple[str, str, str]:
    captured_at = session.get("ended_at") or session.get("started_at")
    if isinstance(captured_at, str) and re.match(r"^\d{4}-\d{2}-\d{2}", captured_at):
        return captured_at, captured_at[:10], captured_at[:7]
    now = datetime.now(timezone.utc).isoformat()
    return now, now[:10], now[:7]


def _ledger_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "ledger" / "import.jsonl"


def _ledger_lock_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "locks" / "import-ledger.lock"


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _LEDGER_THREAD_LOCKS_GUARD:
        lock = _LEDGER_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LEDGER_THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def _locked_ledger(memory_root: Path):
    lock_path = _ledger_lock_path(memory_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _load_recorded(memory_root: Path, key: str) -> dict[str, Any] | None:
    ledger = _ledger_path(memory_root)
    if not ledger.exists():
        return None
    recorded: dict[str, Any] | None = None
    with ledger.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("idempotency_key") != key:
                continue
            if entry.get("status") in {"written", "updated"}:
                recorded = entry
    return recorded


def _append_ledger(memory_root: Path, entry: dict[str, Any]) -> None:
    ledger = _ledger_path(memory_root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _archive_path(memory_root: Path, month: str, key: str, status: str, incoming_hash: str) -> Path:
    archive_dir = memory_root / "archive" / "queue" / month
    stem = f"{safe_key(key)}--{safe_key(status)}--{incoming_hash[:12]}"
    candidate = archive_dir / f"{stem}.json"
    suffix = 2
    while candidate.exists():
        candidate = archive_dir / f"{stem}--{suffix}.json"
        suffix += 1
    return candidate


def _archive_queue(queue_path: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if queue_path.resolve() == archive_path.resolve():
        return
    shutil.copy2(str(queue_path), str(archive_path))


def _remove_queue(queue_path: Path) -> None:
    queue_path.unlink()


def _remove_stale_inbox(previous_inbox_path: str | None, current_inbox_path: Path) -> None:
    if not previous_inbox_path:
        return
    previous = Path(previous_inbox_path)
    if previous == current_inbox_path:
        return
    if previous.exists():
        previous.unlink()


def _decision_entry(
    *,
    status: str,
    key: str,
    queue_path: Path,
    inbox_path: Path,
    archive_path: Path,
    incoming_hash: str,
    incoming_completeness: tuple[int, int, int, int],
    recorded: dict[str, Any] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "idempotency_key": key,
        "queue_path": str(queue_path),
        "inbox_path": str(inbox_path),
        "archive_path": str(archive_path),
        "content_hash": incoming_hash,
        "completeness": list(incoming_completeness),
    }
    if recorded is not None:
        entry["from_completeness"] = recorded.get("completeness")
        entry["to_completeness"] = list(incoming_completeness)
        entry["recorded_hash"] = recorded.get("content_hash")
        entry["incoming_hash"] = incoming_hash
        entry["previous_inbox_path"] = recorded.get("inbox_path")
    return entry


def _persisted_session(session: NormalizedSession, *, raw_payload_pointer: str) -> NormalizedSession:
    persisted: NormalizedSession = dict(session)
    persisted["raw_payload_pointer"] = raw_payload_pointer
    return persisted


def _preview_queue_item_unlocked(queue_item: str | Path, *, memory_root: str | Path) -> dict[str, Any]:
    queue_path = Path(queue_item)
    root = Path(memory_root)
    result = _extract(queue_path)
    session = title.apply(dict(result.session), memory_root=root)
    remote_url = result.raw_payload.get("remote_url") or result.raw_payload.get("remote") or session.get("repo")
    if not isinstance(remote_url, str):
        remote_url = None
    key = idempotency_key(session)
    incoming_hash = content_hash(session, result.capture_scope)
    incoming_completeness = completeness(session, result.capture_scope)
    captured_at, day, month = _date_parts(session)
    bucket = classify_session(session)
    project = resolve_project(
        cwd=session.get("cwd"),
        git_toplevel=session.get("repo"),
        remote_url=remote_url,
        memory_root=str(root),
    )
    inbox_path = root / "inbox" / bucket / session["tool"] / day / f"{safe_key(session['session_id'])}.md"
    recorded = _load_recorded(root, key)
    route_changed = recorded is not None and (
        recorded.get("inbox_path") != str(inbox_path) or recorded.get("project") != project
    )
    if recorded is None:
        status = "written"
    elif route_changed:
        status = "updated"
    elif incoming_hash == recorded.get("content_hash"):
        status = "hash-duplicate"
    elif tuple(incoming_completeness) > tuple(recorded.get("completeness", [])):
        status = "updated"
    else:
        status = "stale-skip"
    archive_path = _archive_path(root, month, key, status, incoming_hash)
    rendered_session = _persisted_session(session, raw_payload_pointer=str(archive_path))
    provenance_repo = normalize_remote(_git.git_remote(_git.git_toplevel(session.get("cwd")))) or "_unknown"
    decision = _decision_entry(
        status=status,
        key=key,
        queue_path=queue_path,
        inbox_path=inbox_path,
        archive_path=archive_path,
        incoming_hash=incoming_hash,
        incoming_completeness=incoming_completeness,
        recorded=recorded,
    )
    decision["classifier_bucket"] = bucket
    decision["project"] = project
    decision["rendered"] = render_markdown(
        rendered_session,
        project=project,
        classifier_bucket=bucket,
        captured_at=captured_at,
        provenance_repo=provenance_repo,
    )
    return decision


def preview_queue_item(queue_item: str | Path, *, memory_root: str | Path) -> dict[str, Any]:
    root = Path(memory_root)
    with _locked_ledger(root):
        return _preview_queue_item_unlocked(queue_item, memory_root=root)


def ingest_queue_item(queue_item: str | Path, *, memory_root: str | Path, dry_run: bool = False) -> dict[str, Any]:
    queue_path = Path(queue_item)
    root = Path(memory_root)
    decision = preview_queue_item(queue_path, memory_root=root)
    rendered = decision.pop("rendered")
    if dry_run:
        decision["dry_run"] = True
        decision["rendered"] = rendered
        return decision

    key = decision["idempotency_key"]
    lock_dir = root / "runtime" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{safe_key(key)}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            with _locked_ledger(root):
                decision = _preview_queue_item_unlocked(queue_path, memory_root=root)
                rendered = decision.pop("rendered")
                inbox_path = Path(decision["inbox_path"])
                archive_path = Path(decision["archive_path"])
                if decision["status"] in {"written", "updated"}:
                    _archive_queue(queue_path, archive_path)
                    _atomic_write(inbox_path, rendered)
                    _remove_stale_inbox(decision.get("previous_inbox_path"), inbox_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
                elif decision["status"] in _TERMINAL_STATUSES:
                    _archive_queue(queue_path, archive_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
            return decision
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
