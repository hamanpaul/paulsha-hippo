"""DEPRECATED: SessionEnd cited/matched `used` recorder (#148).

`record_session_usage` is no longer wired (claude_session_end stopped calling it
with the consumption-loop change); the `used` signal is now read-based via the
PostToolUse(Read) hook. Retained for backward-compat only; may be removed later.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .hooks._wakeup_common import sanitize_id  # single source of the offered-file id sanitizer
from .usage import extract_cited, extract_matched


def _assistant_text(transcript_path: Path) -> str:
    chunks: list[str] = []
    for line in transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text", "")))
    return "\n".join(chunks)


def record_session_usage(root: Path, tool: str, session_id: str, project: str,
                         transcript_path: str | None) -> None:
    try:
        sid = sanitize_id(session_id)
        offered_file = root / "runtime" / "wakeup" / f"{tool}__{sid}.json"
        if not offered_file.exists() or not transcript_path:
            return
        tp = Path(transcript_path)
        if not tp.exists():
            return
        offered = [(o["id"], o.get("title", ""))
                   for o in json.loads(offered_file.read_text(encoding="utf-8")).get("offered", [])]
        offered_ids = [oid for oid, _ in offered]
        text = _assistant_text(tp)
        cited = extract_cited(text, set(offered_ids))
        matched = extract_matched(text, offered, exclude=cited)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id, "tool": tool, "project": project,
            "offered": offered_ids, "cited": sorted(cited), "matched": sorted(matched),
        }
        ledger_dir = root / "runtime" / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        with (ledger_dir / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        return  # best-effort: never break the hook
