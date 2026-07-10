"""Requeue parked sessions back to split（#15 恢復路徑）。

parked 是顯性終態而非死路：backend 修復後由本模組把 session 送回 split，
讓下一輪 promote 重走。事件契約（跨批次共享）：
state="split" + requeued_from="parked" + requeue_reason。
`runtime/queue/_failed/` 證據檔保留（歷史紀錄，不清除）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ledger import processing


def _fragment_count(memory_root: Path, session_key: str) -> int:
    agent, _, session = session_key.partition(":")
    slices_dir = memory_root / "inbox" / "_slices"
    return len(list(slices_dir.rglob(f"{agent}__{session}__*.md")))


def requeue(
    memory_root: Path,
    *,
    session_key: str | None = None,
    all_parked: bool = False,
    now: str,
    reason: str = "",
) -> dict[str, Any]:
    events = processing.fold_events(memory_root)
    if all_parked:
        targets = sorted(
            key for key, event in events.items() if event.get("state") == "parked"
        )
    else:
        targets = [session_key] if session_key else []

    requeued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in targets:
        event = events.get(key)
        state = str(event.get("state", "")) if event else ""
        if state != "parked":
            skipped.append({"session_key": key, "reason": state or "unknown session"})
            continue
        processing.append_state(
            memory_root,
            session_key=key,
            state="split",
            now=now,
            config_hash=str(event.get("atomizer_config_hash", "")),
            requeued_from="parked",
            requeue_reason=reason,
        )
        requeued.append(
            {
                "session_key": key,
                "previous_failure_category": str(event.get("failure_category", "")),
                "fragments": _fragment_count(memory_root, key),
            }
        )
    return {"requeued": requeued, "skipped": skipped}
