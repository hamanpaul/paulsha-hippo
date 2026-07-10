"""Requeue parked sessions back to split（#15 恢復路徑）。

parked 是顯性終態而非死路：backend 修復後由本模組把 session 送回 split，
讓下一輪 promote 重走。事件契約（跨批次共享）：
state="split" + requeued_from="parked" + requeue_reason。
`runtime/queue/_failed/` 證據檔保留（歷史紀錄，不清除）。

Codex 複驗 B2：提交 split「之前」先驗證至少一個可讀且屬於該 session 的
fragment——zero-fragment 的 parked session 一旦 requeue 成 split，pipeline
永遠掃不到它（split 非終態、無 fragment 可 promote），session 就永久卡死。
缺 fragment → 維持 parked、計入 skipped（reason="no-fragments"）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ledger import processing


def _readable_fragment_count(memory_root: Path, session_key: str) -> int:
    """可讀且屬於該 session 的 fragment 數（B2 gate 與 requeue 回報共用）。

    glob 命中但開不了／讀不了（權限、同名目錄、編碼壞檔）不計——gate 要的是
    「requeue 後 pipeline 真的有東西可 promote」。"""
    agent, _, session = session_key.partition(":")
    slices_dir = memory_root / "inbox" / "_slices"
    count = 0
    for path in slices_dir.rglob(f"{agent}__{session}__*.md"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                handle.read(1)
        except (OSError, UnicodeError):
            continue
        count += 1
    return count


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
        fragments = _readable_fragment_count(memory_root, key)
        if fragments == 0:
            # B2 gate：無可讀 fragment 就送回 split = 永久卡非終態；維持 parked。
            skipped.append({"session_key": key, "reason": "no-fragments"})
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
                "fragments": fragments,
            }
        )
    return {"requeued": requeued, "skipped": skipped}
