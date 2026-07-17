"""Hash-pinned, resumable importer recovery from frozen raw queue archives.

Recovery deliberately stops at the importer boundary.  It never invokes the
atomizer and never rewrites the historical import/processing/relations JSONL
ledgers.  A later, explicit replay can consume the recovered inbox documents.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from paulsha_hippo import paths
from paulsha_hippo.importer import backfill


SCHEMA_VERSION = 1
DEFAULT_BATCH_SIZE = 5
_CANARY_NAMES = (
    "hippo-issue-34",
    "health-campaign",
    "labu-pr-2",
    "hippo-claude",
    "homeclaw-claude",
)


class RecoveryError(RuntimeError):
    """Raised when a recovery pin or transaction invariant is violated."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _hash_files(files: Iterable[Path], *, base: Path) -> str:
    rows = []
    for path in sorted({item.resolve() for item in files if item.is_file()}):
        try:
            name = str(path.relative_to(base.resolve()))
        except ValueError:
            name = str(path)
        rows.append((name, _sha_path(path)))
    return _sha_bytes(_canonical(rows))


def _code_hash() -> str:
    package = Path(__file__).resolve().parent
    return _hash_files(package.rglob("*.py"), base=package)


def _config_paths(memory_root: Path) -> list[Path]:
    candidates = [
        Path(__file__).resolve().parent / "atomizer" / "atomizer.yaml",
        paths.projects_config_path(memory_root),
        paths.project_registry_path(memory_root),
        memory_root / "config" / "atomizer.override.yaml",
        memory_root / "config" / "policy.override.yaml",
    ]
    return [path for path in candidates if path.is_file()]


def _pins(memory_root: Path, source_rows: list[dict[str, Any]]) -> dict[str, str]:
    registry_path = paths.project_registry_path(memory_root)
    registry_hash = _sha_path(registry_path) if registry_path.is_file() else _sha_bytes(b"")
    return {
        "code_hash": _code_hash(),
        "config_hash": _hash_files(_config_paths(memory_root), base=memory_root),
        "registry_hash": registry_hash,
        "source_manifest_hash": _sha_bytes(
            _canonical([(row["source"], row["source_hash"]) for row in source_rows])
        ),
    }


def _archive_sources(memory_root: Path) -> list[Path]:
    archive = (memory_root / "archive" / "queue").resolve()
    if not archive.is_dir():
        return []
    result = []
    for path in sorted(archive.rglob("*.json")):
        resolved = path.resolve()
        if not resolved.is_relative_to(archive):
            raise RecoveryError(f"archive source escapes queue root: {path}")
        result.append(resolved)
    return result


def _transcript_pin(payload_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pointer = next(
        (
            payload.get(key)
            for key in ("transcript_path", "transcript", "session_file", "log_path")
            if isinstance(payload.get(key), str) and payload.get(key)
        ),
        None,
    )
    if pointer is None:
        return None
    transcript = Path(pointer).expanduser()
    if not transcript.is_file():
        return {"path": str(transcript), "status": "missing", "sha256": None}
    return {"path": str(transcript.resolve()), "status": "verified", "sha256": _sha_path(transcript)}


def _canary(payload: bytes) -> tuple[int, str | None]:
    text = payload.decode("utf-8", errors="ignore").lower()
    matches = (
        "hippo" in text
        and any(term in text for term in ("issue #34", "issue-34", "atomization release")),
        any(term in text for term in ("health-integrator", "health campaign")),
        "labu" in text and any(term in text for term in ("pr #2", "pull/2", "pr-2")),
        "paulsha-hippo" in text and "claude" in text,
        "homeclaw" in text and "claude" in text,
    )
    for index, matched in enumerate(matches):
        if matched:
            return index, _CANARY_NAMES[index]
    return len(_CANARY_NAMES), None


def _winner_score(candidate: dict[str, Any]) -> tuple[int, int, str, str]:
    scope_rank = {"turn": 0, "subagent": 0, "pre_compact": 1, "session_end": 2, "watcher_final": 3}
    content_size = sum(len(str(item)) for item in candidate.get("assistant_messages", []))
    content_size += sum(len(str(item)) for item in candidate.get("user_prompts", []))
    return (
        scope_rank.get(str(candidate.get("capture_scope")), 0),
        content_size,
        str(candidate.get("ended_at") or ""),
        str(candidate.get("source") or ""),
    )


def _read_processing_states(memory_root: Path) -> dict[str, str]:
    from paulsha_hippo.ledger import processing

    try:
        return processing.fold_states(memory_root)
    except Exception:  # noqa: BLE001 - planning records corrupt-state evidence per entry
        return {}


def _write_fsync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def create_plan(
    memory_root: str | Path,
    *,
    manifest_path: str | Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    baseline_count: int | None = None,
) -> Path:
    """Account for every frozen raw payload and write an immutable recovery plan."""
    if batch_size <= 0:
        raise RecoveryError("batch_size must be positive")
    if baseline_count is not None and baseline_count < 0:
        raise RecoveryError("baseline_count must be non-negative")
    root = Path(memory_root).resolve()
    sources = _archive_sources(root)
    chronological_sources = sorted(
        sources, key=lambda source: (source.stat().st_mtime_ns, str(source))
    )
    baseline_sources = set(
        chronological_sources[:baseline_count]
        if baseline_count is not None
        else chronological_sources
    )
    source_rows = [
        {
            "source": str(source),
            "source_hash": _sha_path(source),
            "source_bytes": source.stat().st_size,
            "source_set": "baseline" if source in baseline_sources else "ingress-drift",
            "transcript": _transcript_pin(source),
        }
        for source in sources
    ]
    preliminary_id = _sha_bytes(_canonical([(row["source"], row["source_hash"]) for row in source_rows]))[:16]
    recovery_root = root / "runtime" / "recovery" / preliminary_id
    planned_root = recovery_root / "planned"
    candidates: list[dict[str, Any]] = []
    source_by_path = {row["source"]: row for row in source_rows}
    for source in sources:
        row = source_by_path[str(source)]
        try:
            candidate = backfill.prepare_reextract(
                source, root, allow_title_backend=False
            )
            rendered = str(candidate.pop("rendered")).encode("utf-8")
            candidate.update(row)
            candidate["source"] = str(source)
            candidate["new_hash"] = _sha_bytes(rendered)
            candidate["new_bytes"] = len(rendered)
            candidate["planned_artifact"] = str(planned_root / f"{row['source_hash']}.md")
            _write_fsync(Path(candidate["planned_artifact"]), rendered)
            target = Path(candidate["inbox_path"])
            candidate["old_hash"] = _sha_path(target) if target.is_file() else None
            candidate["old_bytes"] = target.stat().st_size if target.is_file() else 0
            priority, canary = _canary(source.read_bytes())
            candidate["canary"] = canary
            candidate["canary_priority"] = priority
            candidates.append(candidate)
        except Exception as exc:  # noqa: BLE001 - every raw source remains accounted for
            candidates.append(
                {
                    **row,
                    "source": str(source),
                    "decision": "parked-with-reason",
                    "reason": type(exc).__name__,
                    "logical_session_key": None,
                    "winner": False,
                    "expected_ledger_delta": {"recovery_journal": 0, "historical_jsonl": 0},
                }
            )

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        logical_key = candidate.get("logical_session_key")
        if isinstance(logical_key, str):
            groups[logical_key].append(candidate)
    states = _read_processing_states(root)
    for logical_key, group in groups.items():
        winner = max(group, key=_winner_score)
        canary_evidence = sorted(
            (item for item in group if item.get("canary") is not None),
            key=lambda item: int(item.get("canary_priority", len(_CANARY_NAMES))),
        )
        if canary_evidence:
            winner["canary"] = canary_evidence[0]["canary"]
            winner["canary_priority"] = canary_evidence[0]["canary_priority"]
            winner["canary_source"] = canary_evidence[0]["source"]
        for candidate in group:
            candidate["winner"] = candidate is winner
            candidate["atomizer_state"] = states.get(logical_key, "unseen")
            candidate["llm_replay"] = "not-planned"
            candidate["expected_ledger_delta"] = {
                "recovery_journal": 6 if candidate is winner else 0,
                "historical_jsonl": 0,
            }
            if candidate is winner:
                risk_reasons = []
                if candidate.get("old_hash") is None:
                    risk_reasons.append("missing-inbox")
                elif candidate.get("old_hash") != candidate.get("new_hash"):
                    risk_reasons.append("changed-content")
                if candidate.get("atomizer_state") == "parked":
                    risk_reasons.append("parked")
                if len(candidate.get("assistant_messages", [])) > 1:
                    risk_reasons.append("multiple-assistant-outcomes")
                if any(
                    len(str(message)) > 2000
                    for message in candidate.get("assistant_messages", [])
                ):
                    risk_reasons.append("long-assistant-outcome")
                candidate["risk_reasons"] = risk_reasons
                candidate["decision"] = "importer-recover"
                candidate["disposition"] = "recovered-planned"
            else:
                candidate["decision"] = "superseded-source"
                candidate["disposition"] = "accounted-no-write"
                candidate["winner_source"] = winner["source"]

    winners = [candidate for candidate in candidates if candidate.get("winner")]
    winners.sort(
        key=lambda item: (
            int(item.get("canary_priority", len(_CANARY_NAMES))),
            0 if item.get("old_hash") != item.get("new_hash") else 1,
            0 if item.get("atomizer_state") == "parked" else 1,
            str(item.get("ended_at") or ""),
            str(item.get("source") or ""),
        )
    )
    # Seed the first batch with at most one representative from each named canary
    # family before the normal changed-content/parked/age backlog ordering.
    seeded: list[dict[str, Any]] = []
    seeded_hashes: set[str] = set()
    for canary_name in _CANARY_NAMES:
        candidate = next(
            (item for item in winners if item.get("canary") == canary_name),
            None,
        )
        if candidate is not None:
            seeded.append(candidate)
            seeded_hashes.add(str(candidate["source_hash"]))
    winners = seeded + [
        item for item in winners if str(item["source_hash"]) not in seeded_hashes
    ]
    order = {item["source_hash"]: index for index, item in enumerate(winners)}
    for candidate in candidates:
        candidate["plan_order"] = order.get(candidate["source_hash"])

    pins = _pins(root, source_rows)
    recovery_id = _sha_bytes(_canonical({"pins": pins, "batch_size": batch_size}))[:20]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "recovery_id": recovery_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "memory_root": str(root),
        "transaction_root": str(recovery_root),
        "batch_size": batch_size,
        "pins": pins,
        "source_count": len(source_rows),
        "baseline_source_count": len(baseline_sources),
        "ingress_drift_count": len(source_rows) - len(baseline_sources),
        "logical_session_count": len(groups),
        "winner_count": len(winners),
        "llm_replay": "not-planned",
        "expected_batch_journal_delta": 2 if winners else 0,
        "entries": sorted(candidates, key=lambda item: str(item["source"])),
    }
    destination = Path(manifest_path) if manifest_path is not None else recovery_root / "manifest.json"
    _write_fsync(destination, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    return destination


def _load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"cannot read recovery manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise RecoveryError("unsupported recovery manifest")
    return manifest


def _verify_pins(manifest: dict[str, Any]) -> Path:
    root = Path(str(manifest["memory_root"])).resolve()
    archive_root = (root / "archive" / "queue").resolve()
    inbox_root = (root / "inbox").resolve()
    recovery_root = (root / "runtime" / "recovery").resolve()
    transaction_root = Path(str(manifest.get("transaction_root") or "")).resolve()
    if not transaction_root.is_relative_to(recovery_root):
        raise RecoveryError("transaction_root escapes runtime/recovery")
    source_rows = []
    for entry in manifest.get("entries", []):
        source = Path(str(entry["source"])).resolve()
        if not source.is_relative_to(archive_root):
            raise RecoveryError(f"source escapes frozen archive: {source}")
        expected = str(entry["source_hash"])
        if not source.is_file() or _sha_path(source) != expected:
            raise RecoveryError(f"source pin drift: {source}")
        source_rows.append({"source": str(source), "source_hash": expected})
        transcript = entry.get("transcript")
        if isinstance(transcript, dict) and transcript.get("status") == "verified":
            pointer = Path(str(transcript["path"]))
            if not pointer.is_file() or _sha_path(pointer) != transcript.get("sha256"):
                raise RecoveryError(f"transcript pin drift: {pointer}")
        if entry.get("decision") == "importer-recover":
            target = Path(str(entry.get("inbox_path") or "")).resolve()
            artifact = Path(str(entry.get("planned_artifact") or "")).resolve()
            if not target.is_relative_to(inbox_root):
                raise RecoveryError(f"recovery target escapes inbox: {target}")
            if not artifact.is_relative_to(transaction_root / "planned"):
                raise RecoveryError(f"planned artifact escapes transaction: {artifact}")
    actual = _pins(root, sorted(source_rows, key=lambda row: row["source"]))
    for key, expected in dict(manifest.get("pins", {})).items():
        if actual.get(key) != expected:
            raise RecoveryError(f"{key} drift: expected {expected}, got {actual.get(key)}")
    return root


def _journal_path(manifest: dict[str, Any]) -> Path:
    return Path(str(manifest["transaction_root"])) / "journal.jsonl"


def _append_journal(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _canonical(event) + b"\n"
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecoveryError(f"malformed recovery journal line {number}") from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def _fsync_parent(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _transaction_lock(manifest: dict[str, Any]):
    lock_path = Path(str(manifest["transaction_root"])) / "recovery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _active_committed_hashes(events: list[dict[str, Any]]) -> set[str]:
    active: dict[str, str] = {}
    for event in events:
        source_hash = str(event.get("source_hash") or "")
        batch_id = str(event.get("batch_id") or "")
        if not source_hash or not batch_id:
            continue
        if event.get("event") == "committed":
            active[source_hash] = batch_id
        elif event.get("event") == "rolled_back" and active.get(source_hash) == batch_id:
            active.pop(source_hash, None)
    return set(active)


def _target_hash(target: Path) -> str | None:
    if not target.exists() and not target.is_symlink():
        return None
    if target.is_symlink() or not target.is_file():
        raise RecoveryError(f"target precondition drift: unsafe target {target}")
    return _sha_path(target)


def _verify_target_precondition(
    entry: dict[str, Any], target: Path, item_events: list[dict[str, Any]]
) -> str | None:
    actual = _target_hash(target)
    old_hash = entry.get("old_hash")
    new_hash = str(entry["new_hash"])
    phases = {str(event.get("event")) for event in item_events}
    if "replaced" in phases or "committed" in phases:
        allowed = {new_hash}
    elif "replace_intent" in phases:
        allowed = {old_hash, new_hash}
    else:
        allowed = {old_hash}
    if actual not in allowed:
        raise RecoveryError(
            f"target precondition drift: {target}: expected {sorted(str(value) for value in allowed)}, "
            f"got {actual}"
        )
    return actual


def _selected_entries(
    manifest: dict[str, Any], events: list[dict[str, Any]], *, resume: bool
) -> tuple[str, list[dict[str, Any]]]:
    completed = _active_committed_hashes(events)
    batches_done = {
        str(event.get("batch_id"))
        for event in events
        if event.get("event") in {"batch_done", "batch_rolled_back"}
    }
    if resume:
        for event in reversed(events):
            batch_id = str(event.get("batch_id") or "")
            if event.get("event") == "batch_started" and batch_id and batch_id not in batches_done:
                raw_hashes = event.get("source_hashes")
                if not isinstance(raw_hashes, list) or not all(
                    isinstance(item, str) and item for item in raw_hashes
                ):
                    raise RecoveryError(f"invalid batch membership journal: {batch_id}")
                hashes = set(raw_hashes)
                selected = [
                    entry for entry in manifest["entries"] if entry.get("source_hash") in hashes
                ]
                if len(selected) != len(hashes):
                    raise RecoveryError(f"batch membership is not present in manifest: {batch_id}")
                return batch_id, sorted(selected, key=lambda item: int(item["plan_order"]))
    eligible = [
        entry
        for entry in manifest["entries"]
        if entry.get("decision") == "importer-recover"
        and entry.get("source_hash") not in completed
    ]
    eligible.sort(key=lambda item: int(item["plan_order"]))
    selected = eligible[: int(manifest.get("batch_size", DEFAULT_BATCH_SIZE))]
    return uuid.uuid4().hex, selected


def apply_plan(
    manifest_path: str | Path,
    *,
    resume: bool = False,
    _interrupt_after: str | None = None,
) -> dict[str, Any]:
    """Apply or resume one bounded batch; pins are rechecked before every mutation."""
    path = Path(manifest_path).resolve()
    manifest = _load_manifest(path)
    _verify_pins(manifest)
    with _transaction_lock(manifest):
        manifest = _load_manifest(path)
        _verify_pins(manifest)
        journal = _journal_path(manifest)
        events = _read_journal(journal)

        def record(event: dict[str, Any]) -> None:
            _append_journal(journal, event)
            events.append(event)

        batch_id, selected = _selected_entries(manifest, events, resume=resume)
        if not selected:
            return {
                "batch_id": None,
                "committed": 0,
                "complete": len(_active_committed_hashes(events))
                >= int(manifest.get("winner_count", 0)),
            }
        if not any(
            event.get("event") == "batch_started" and event.get("batch_id") == batch_id
            for event in events
        ):
            record(
                {
                    "event": "batch_started",
                    "batch_id": batch_id,
                    "source_hashes": [str(entry["source_hash"]) for entry in selected],
                }
            )

        committed = 0
        for entry in selected:
            source_hash = str(entry["source_hash"])
            item_events = [
                event
                for event in events
                if event.get("batch_id") == batch_id
                and event.get("source_hash") == source_hash
            ]
            if any(event.get("event") == "committed" for event in item_events):
                continue
            target = Path(str(entry["inbox_path"]))
            current_hash = _verify_target_precondition(entry, target, item_events)
            tx_root = (
                Path(str(manifest["transaction_root"]))
                / "batches"
                / batch_id
                / source_hash
            )
            preimage = tx_root / "preimage.bin"
            staging = tx_root / "staging.md"
            if not any(event.get("event") == "begin" for event in item_events):
                event = {"event": "begin", "batch_id": batch_id, "source_hash": source_hash}
                record(event)
                item_events.append(event)
                if _interrupt_after == "begin":
                    raise RecoveryError("injected interruption after begin")

            prior_preimage = next(
                (event for event in reversed(item_events) if event.get("event") == "preimage"),
                None,
            )
            if prior_preimage is None:
                existed = entry.get("old_hash") is not None
                if existed:
                    _write_fsync(preimage, target.read_bytes())
                event = {
                    "event": "preimage",
                    "batch_id": batch_id,
                    "source_hash": source_hash,
                    "existed": existed,
                    "old_hash": entry.get("old_hash"),
                }
                record(event)
                item_events.append(event)
            else:
                if prior_preimage.get("old_hash") != entry.get("old_hash"):
                    raise RecoveryError(f"preimage journal drift: {preimage}")
                if prior_preimage.get("existed"):
                    if not preimage.is_file() or _sha_path(preimage) != entry.get("old_hash"):
                        raise RecoveryError(f"missing or drifted pinned preimage: {preimage}")
            if _interrupt_after == "preimage":
                raise RecoveryError("injected interruption after preimage")

            planned = Path(str(entry["planned_artifact"]))
            if not planned.is_file() or _sha_path(planned) != entry["new_hash"]:
                raise RecoveryError(f"planned artifact drift: {planned}")

            phases = {str(event.get("event")) for event in item_events}
            if "replaced" not in phases:
                if "replace_intent" in phases and current_hash == entry["new_hash"]:
                    event = {
                        "event": "replaced",
                        "batch_id": batch_id,
                        "source_hash": source_hash,
                        "new_hash": entry["new_hash"],
                    }
                    record(event)
                    item_events.append(event)
                else:
                    _write_fsync(staging, planned.read_bytes())
                    event = {"event": "staged", "batch_id": batch_id, "source_hash": source_hash}
                    record(event)
                    item_events.append(event)
                    if _interrupt_after == "staged":
                        raise RecoveryError("injected interruption after staged")
                    event = {
                        "event": "replace_intent",
                        "batch_id": batch_id,
                        "source_hash": source_hash,
                        "old_hash": entry.get("old_hash"),
                        "new_hash": entry["new_hash"],
                    }
                    record(event)
                    item_events.append(event)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staging, target)
                    with target.open("rb") as handle:
                        os.fsync(handle.fileno())
                    _fsync_parent(target)
                    event = {
                        "event": "replaced",
                        "batch_id": batch_id,
                        "source_hash": source_hash,
                        "new_hash": entry["new_hash"],
                    }
                    record(event)
                    item_events.append(event)
            if _interrupt_after == "replace":
                raise RecoveryError("injected interruption after replace")
            event = {
                "event": "committed",
                "batch_id": batch_id,
                "source_hash": source_hash,
                "target": str(target),
                "new_hash": entry["new_hash"],
            }
            record(event)
            committed += 1
            if _interrupt_after == "committed":
                raise RecoveryError("injected interruption after committed")
        record({"event": "batch_done", "batch_id": batch_id})
        return {
            "batch_id": batch_id,
            "committed": committed,
            "complete": len(_active_committed_hashes(events))
            >= int(manifest.get("winner_count", 0)),
        }


def rollback_plan(manifest_path: str | Path) -> dict[str, Any]:
    """Compensate only the most recent applied batch, preserving historical JSONL."""
    path = Path(manifest_path).resolve()
    manifest = _load_manifest(path)
    _verify_pins(manifest)
    with _transaction_lock(manifest):
        manifest = _load_manifest(path)
        _verify_pins(manifest)
        journal = _journal_path(manifest)
        events = _read_journal(journal)
        rolled_batches = {
            str(event.get("batch_id"))
            for event in events
            if event.get("event") == "batch_rolled_back"
        }
        batch_id = next(
            (
                str(event.get("batch_id"))
                for event in reversed(events)
                if event.get("event") == "committed"
                and str(event.get("batch_id")) not in rolled_batches
            ),
            None,
        )
        if batch_id is None:
            return {"batch_id": None, "rolled_back": 0}
        by_hash = {str(entry["source_hash"]): entry for entry in manifest["entries"]}
        commits = [
            event
            for event in events
            if event.get("event") == "committed" and event.get("batch_id") == batch_id
        ]
        rolled_back = 0
        for event in reversed(commits):
            source_hash = str(event["source_hash"])
            entry = by_hash[source_hash]
            target = Path(str(entry["inbox_path"]))
            if _target_hash(target) != entry["new_hash"]:
                raise RecoveryError(f"rollback target drift: {target}")
            preimage_event = next(
                (
                    item
                    for item in reversed(events)
                    if item.get("event") == "preimage"
                    and item.get("batch_id") == batch_id
                    and item.get("source_hash") == source_hash
                ),
                None,
            )
            if preimage_event is None:
                raise RecoveryError(f"missing preimage journal: {source_hash}")
            preimage = (
                Path(str(manifest["transaction_root"]))
                / "batches"
                / batch_id
                / source_hash
                / "preimage.bin"
            )
            if preimage_event.get("existed"):
                if not preimage.is_file() or _sha_path(preimage) != entry.get("old_hash"):
                    raise RecoveryError(f"missing or drifted pinned preimage: {preimage}")
                staging = preimage.with_name("rollback.md")
                _write_fsync(staging, preimage.read_bytes())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
                _fsync_parent(target)
            elif target.exists():
                target.unlink()
                _fsync_parent(target)
            rollback_event = {
                "event": "rolled_back",
                "batch_id": batch_id,
                "source_hash": source_hash,
            }
            _append_journal(journal, rollback_event)
            events.append(rollback_event)
            rolled_back += 1
        _append_journal(journal, {"event": "batch_rolled_back", "batch_id": batch_id})
        return {"batch_id": batch_id, "rolled_back": rolled_back}
