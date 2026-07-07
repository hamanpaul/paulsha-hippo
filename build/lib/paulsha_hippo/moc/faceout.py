# paulshaclaw/memory/moc/faceout.py
from __future__ import annotations

from pathlib import Path

from ..ledger import lifecycle


def mark_faceout(memory_root: Path) -> None:
    wiki = memory_root / "knowledge" / "wiki-moc.md"
    if not wiki.exists():
        return
    decayed: dict[str, tuple[str, str]] = {}  # record_id -> (reason, ts), latest wins
    for event in lifecycle.read_events(memory_root):
        if event.get("event_type") == "decayed":
            rid = event.get("record_id")
            if rid:
                decayed[str(rid)] = (str(event.get("reason", "")), str(event.get("ts", "")))
    lines = ["", "## Faceout", ""]
    for rid, (reason, ts) in sorted(decayed.items()):
        lines.append(f"- {rid} — decayed: {reason}, since {ts}")
    with wiki.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
