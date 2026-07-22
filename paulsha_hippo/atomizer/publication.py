"""Recoverable per-session publication transaction for semantic atoms."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..ledger import processing, relations


class PublicationError(RuntimeError):
    """Publication failed before the session became eligible."""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _journal_path(root: Path) -> Path:
    return root / "runtime" / "ledger" / "publication.jsonl"


def _append(root: Path, event: Mapping[str, Any]) -> None:
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


@dataclass(frozen=True)
class PublicationItem:
    slice_id: str
    target: Path
    data: bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "target": str(self.target),
            "sha256": _sha(self.data),
            "size": len(self.data),
        }


class PublicationTransaction:
    """Stage, materialize, and commit one immutable atom set."""

    def __init__(
        self,
        memory_root: str | Path,
        *,
        publication_id: str,
        session_key: str,
        now: str,
        config_hash: str,
        stage_root: str | Path | None = None,
    ) -> None:
        self.memory_root = Path(memory_root)
        self.publication_id = publication_id
        self.session_key = session_key
        self.now = now
        self.config_hash = config_hash
        self.stage_root = Path(stage_root) if stage_root else self.memory_root / "runtime" / "staging" / "atomize" / publication_id

    def prepare(
        self,
        items: Sequence[PublicationItem],
        edges: Sequence[Mapping[str, str]],
        *,
        processing_extra: Mapping[str, Any] | None = None,
    ) -> None:
        if not items:
            raise PublicationError("cannot publish an empty session")
        self.stage_root.mkdir(parents=True, exist_ok=True)
        descriptors: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if item.target.exists() and item.target.read_bytes() != item.data:
                raise PublicationError(f"slice publication collision at {item.target}")
            stage = self.stage_root / f"{index:04d}.md"
            with stage.open("wb") as handle:
                handle.write(item.data)
                handle.flush()
                os.fsync(handle.fileno())
            descriptors.append({**item.as_dict(), "stage": str(stage)})
        _fsync_parent(self.stage_root / "0000.md")
        _append(
            self.memory_root,
            {
                "event": "publish_prepare",
                "publication_id": self.publication_id,
                "session_key": self.session_key,
                "now": self.now,
                "config_hash": self.config_hash,
                "items": descriptors,
                "edges": [dict(edge) for edge in edges],
                "processing_extra": dict(processing_extra or {}),
            },
        )

    def materialize(self) -> None:
        events = _events(self.memory_root)
        prepare = next((event for event in reversed(events) if event.get("publication_id") == self.publication_id and event.get("event") == "publish_prepare"), None)
        if prepare is None:
            raise PublicationError("publication has no prepare record")
        for item in prepare["items"]:
            target = Path(str(item["target"]))
            stage = Path(str(item["stage"]))
            expected = str(item["sha256"])
            if target.exists():
                if _sha(target.read_bytes()) != expected:
                    raise PublicationError(f"publication target drift at {target}")
                if stage.exists():
                    stage.unlink()
                continue
            if not stage.exists() or _sha(stage.read_bytes()) != expected:
                raise PublicationError(f"publication staging drift at {stage}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, target)
            _fsync_parent(target)
        _append(self.memory_root, {"event": "publish_materialized", "publication_id": self.publication_id, "now": self.now})

    def commit(self) -> None:
        events = _events(self.memory_root)
        if any(event.get("event") == "publish_commit" and event.get("publication_id") == self.publication_id for event in events):
            shutil.rmtree(self.stage_root, ignore_errors=True)
            return
        prepare = next((event for event in reversed(events) if event.get("publication_id") == self.publication_id and event.get("event") == "publish_prepare"), None)
        if prepare is None:
            raise PublicationError("publication has no prepare record")
        committed = False
        try:
            self.materialize_if_needed(events)
            relations.append_edges(
                self.memory_root,
                [dict(edge) for edge in prepare.get("edges", [])],
                now=self.now,
                config_hash=self.config_hash,
                publication_id=self.publication_id,
            )
            _append(self.memory_root, {"event": "publish_commit", "publication_id": self.publication_id, "now": self.now})
            committed = True
            extra = dict(prepare.get("processing_extra") or {})
            if processing.state_of(self.memory_root, self.session_key) != "promoted":
                processing.append_state(
                    self.memory_root,
                    session_key=self.session_key,
                    state="promoted",
                    now=self.now,
                    config_hash=self.config_hash,
                    publication_id=self.publication_id,
                    **extra,
                )
        except Exception:
            # A relation writer can fail after appending its bytes.  Keep the
            # append-only ledgers intact, but remove only target files whose
            # bytes are exactly owned by this prepare record.  Their relation
            # rows carry publication_id and remain invisible until a commit
            # marker is durable; the next run can safely retry the same tx.
            if not committed:
                for item in prepare.get("items", []):
                    target = Path(str(item.get("target", "")))
                    expected = str(item.get("sha256", ""))
                    try:
                        if target.is_file() and _sha(target.read_bytes()) == expected:
                            target.unlink()
                    except OSError:
                        pass
                shutil.rmtree(self.stage_root, ignore_errors=True)
            raise
        finally:
            if committed:
                shutil.rmtree(self.stage_root, ignore_errors=True)

    def materialize_if_needed(self, events: Sequence[Mapping[str, Any]] | None = None) -> None:
        events = list(events or _events(self.memory_root))
        if not any(event.get("event") == "publish_materialized" and event.get("publication_id") == self.publication_id for event in events):
            self.materialize()


def _events(root: Path) -> list[dict[str, Any]]:
    path = _journal_path(root)
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def committed_publication_ids(memory_root: str | Path) -> set[str]:
    """Return publication IDs with a durable commit marker.

    MOC/index readers use this small read-only projection as an eligibility
    guard.  A target may already be present after a crash between materialize
    and commit; without the marker it must remain invisible to consumers.
    """
    return {
        str(event["publication_id"])
        for event in _events(Path(memory_root))
        if event.get("event") == "publish_commit"
        and isinstance(event.get("publication_id"), str)
    }


def _processing_owned_promoted(root: Path, session_key: str, publication_id: str) -> bool:
    """Whether this transaction owns the current promoted state."""
    for event in reversed(processing.read_events(root)):
        if event.get("session_key") != session_key:
            continue
        return (
            event.get("state") == "promoted"
            and event.get("publication_id") == publication_id
        )
    return False


def recover_incomplete(memory_root: str | Path) -> dict[str, Any]:
    """Finish safe prepared publications or roll back only matching artifacts."""
    root = Path(memory_root)
    events = _events(root)
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        publication_id = event.get("publication_id")
        if not isinstance(publication_id, str):
            continue
        by_id.setdefault(publication_id, {})
        by_id[publication_id][str(event.get("event"))] = event
    recovered: list[str] = []
    rolled_back: list[str] = []
    repaired: list[str] = []
    for publication_id, state in sorted(by_id.items()):
        if "publish_prepare" not in state:
            continue
        prepare = state["publish_prepare"]
        if "publish_commit" in state:
            # A crash after the commit marker but before the processing ledger
            # append leaves a durable publication with no current promoted
            # state.  The marker is authoritative, so repair only that
            # transaction's missing processing event rather than reopening it.
            # If the entire processing ledger is absent, there is no local
            # evidence that this publication belongs to the current recovery
            # run (an operator may intentionally have reset that ledger for a
            # replay).  Leave the immutable publication alone and let the
            # normal ingress state machine establish a fresh split event.
            if not processing.processing_path(root).exists():
                continue
            session_key = str(prepare.get("session_key", ""))
            if processing.state_of(root, session_key) != "promoted":
                extra = dict(prepare.get("processing_extra") or {})
                processing.append_state(
                    root,
                    session_key=session_key,
                    state="promoted",
                    now=str(prepare.get("now", "")),
                    config_hash=str(prepare.get("config_hash", "")),
                    publication_id=publication_id,
                    **extra,
                )
                repaired.append(publication_id)
            continue
        items = prepare.get("items", [])
        all_safe = True
        for item in items:
            target = Path(str(item.get("target", "")))
            expected = str(item.get("sha256", ""))
            if not target.is_file() or _sha(target.read_bytes()) != expected:
                all_safe = False
                break
        tx = PublicationTransaction(
            root,
            publication_id=publication_id,
            session_key=str(prepare.get("session_key", "")),
            now=str(prepare.get("now", "")),
            config_hash=str(prepare.get("config_hash", "")),
            stage_root=root / "runtime" / "staging" / "atomize" / publication_id,
        )
        if all_safe:
            try:
                tx.commit()
            except Exception:
                all_safe = False
            else:
                recovered.append(publication_id)
                continue
        for item in items:
            target = Path(str(item.get("target", "")))
            expected = str(item.get("sha256", ""))
            try:
                if target.is_file() and _sha(target.read_bytes()) == expected:
                    target.unlink()
            except OSError:
                pass
        shutil.rmtree(tx.stage_root, ignore_errors=True)
        if _processing_owned_promoted(root, str(prepare.get("session_key", "")), publication_id):
            processing.append_state(
                root,
                session_key=str(prepare.get("session_key", "")),
                state="split",
                now=str(prepare.get("now", "")),
                config_hash=str(prepare.get("config_hash", "")),
                publication_rollback=publication_id,
            )
        _append(root, {"event": "publish_rollback", "publication_id": publication_id, "now": prepare.get("now", "")})
        rolled_back.append(publication_id)
    result: dict[str, Any] = {"recovered": recovered, "rolled_back": rolled_back}
    if repaired:
        result["repaired"] = repaired
    return result


__all__ = [
    "PublicationError",
    "PublicationItem",
    "PublicationTransaction",
    "committed_publication_ids",
    "recover_incomplete",
]
