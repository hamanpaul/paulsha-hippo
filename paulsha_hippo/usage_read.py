"""``hippo show`` 的 read 事件寫入器：與 hooks/claude_post_tool_use.py 同 schema。

memory-consumer：`hippo show --agent --tool T --session-id S` 是 Read 以外的
另一條讀取路徑（省 token 的精簡視圖）——但省下的 Read 不能連帶讓
memory-usage KPI（看過率）失真，所以這裡複用同一批共用構點
(`offered_map_path`) 產生與 hook 完全相同 schema 的 read 事件，讓
`ledger/usage.py` 的 reader 對兩者一視同仁。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from paulsha_hippo.hooks._wakeup_common import offered_map_path


def append_read_event(
    root: Path, *, tool: str, session_id: str, sl_id: str, path: Path, project: str
) -> dict:
    """Append 一筆 read 事件到 ``runtime/ledger/memory_usage.jsonl``。

    ``offered`` 由 per-session offered map（``offered_map_path`` 的 ``by_id``）
    決定：sl_id 在該 session 的 offered 集合內即 True。查不到/檔案壞掉一律
    fail-soft 為 False（歸因寧缺勿誤標）。
    """
    offered = False
    try:
        by_id = json.loads(
            offered_map_path(root, tool, session_id).read_text(encoding="utf-8")
        ).get("by_id", {})
        offered = sl_id in by_id
    except Exception:
        offered = False
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "tool": tool,
        "project": project,
        "sl_id": sl_id,
        "path": str(path),
        "source": "read",
        "offered": offered,
    }
    led = root / "runtime" / "ledger"
    led.mkdir(parents=True, exist_ok=True)
    with (led / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev
