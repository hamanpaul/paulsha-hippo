"""Requeue parked sessions back to split（#15 恢復路徑）。

parked 是顯性終態而非死路：backend 修復後由本模組把 session 送回 split，
讓下一輪 promote 重走。事件契約（跨批次共享）：
state="split" + requeued_from="parked" + requeue_reason。
`runtime/queue/_failed/` 證據檔保留（歷史紀錄，不清除）。

Codex 複驗 B2：提交 split「之前」先驗證至少一個「pipeline 真的讀得動、且
frontmatter 屬於該 session」的 fragment。只 glob 檔名＋讀 1 char 不夠——壞檔／
截斷／缺 frontmatter 欄位的 fragment，pipeline `_read_fragment()` 讀不出，送回
split 只會每輪警告、永久卡非終態；檔名對得上但 frontmatter 指向別 session 的
fragment，則會把別人的內容錯 promote。gate 沿用 pipeline 同一套 `_read_fragment()`
（不另寫平行解析，避免同源漂移），驗證必要欄位（project／source_session）與
source_agent／source_session 相符。gate 不過 → 維持 parked、計入 skipped
（reason="no-valid-fragments"）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .atomizer import pipeline as atomizer_pipeline
from .ledger import processing


def _valid_fragment_count(memory_root: Path, session_key: str) -> int:
    """屬於該 session 且 pipeline 真的讀得動的 fragment 數（B2 gate 與回報共用）。

    沿用 pipeline 的 `_read_fragment`（同一套 frontmatter 解析／欄位契約，杜絕
    平行邏輯同源漂移）：檔名 glob 命中後，還要 frontmatter 解析得出、含必要
    欄位（project／source_session）、且 source_agent／source_session 與目標
    session 相符才算數。缺欄位、壞檔、或檔名對得上但內容屬於別 session
    （`_read_fragment` 讀得出卻不匹配）皆不計——gate 要的是 requeue 成 split
    後 pipeline 真有「屬於該 session」的東西可 promote，而非空轉卡非終態。"""
    agent, _, session = session_key.partition(":")
    slices_dir = memory_root / "inbox" / "_slices"
    count = 0
    for path in slices_dir.rglob(f"{agent}__{session}__*.md"):
        try:
            fragment = atomizer_pipeline._read_fragment(path)
        except (OSError, UnicodeError, ValueError, TypeError):
            # glob 命中但讀不了（權限、同名目錄、編碼壞檔），或 frontmatter 欄位
            # 型別壞到 _read_fragment 自身 raise——例如 fragment_index 為 null／
            # 非純量令 int(None) 拋 TypeError、非數字字串令 int("x") 拋 ValueError——
            # pipeline 也會在同一 fragment 上卡住，一律不計（落入 no-valid-fragments
            # skip 路徑，維持 parked），不讓例外逃出整個 requeue() 連坐其他 session。
            continue
        if fragment is None:
            continue
        if fragment.source_agent != agent or fragment.source_session != session:
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
    _after_snapshot: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """把 parked session 送回 split。

    進入時的 fold 快照只用來「挑選目標」與跑檔案系統 fragment 閘（不需原子性）；
    真正的 parked→split 轉移一律經 `processing.transition_state_atomic` 於同一把
    exclusive lock 內「重讀＋重新確認狀態＋拒絕 stale ts＋fold 後驗證」原子提交——
    快照後被另一 writer promote/park、或帶較舊 `now` 的呼叫，都會被拒絕並計入
    skipped，而非錯誤復活已 promote 的 session 或回報未生效的 false-success。

    `_after_snapshot` 為測試接縫（併發回歸用）：於取得快照後、任何轉移前觸發一次，
    讓測試注入「快照後才發生」的併發寫入，驗證轉移確實以持鎖重讀為準而非信任快照。
    """
    events = processing.fold_events(memory_root)
    if all_parked:
        targets = sorted(
            key for key, event in events.items() if event.get("state") == "parked"
        )
    else:
        targets = [session_key] if session_key else []

    if _after_snapshot is not None:
        _after_snapshot()

    requeued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in targets:
        event = events.get(key)
        state = str(event.get("state", "")) if event else ""
        if state != "parked":
            skipped.append({"session_key": key, "reason": state or "unknown session"})
            continue
        fragments = _valid_fragment_count(memory_root, key)
        if fragments == 0:
            # B2 gate：無「可讀且屬於該 session」的 fragment 就送回 split =
            # 永久卡非終態（或錯 promote 別 session）；維持 parked。
            skipped.append({"session_key": key, "reason": "no-valid-fragments"})
            continue
        ok, refusal = processing.transition_state_atomic(
            memory_root,
            session_key=key,
            expected_states=("parked",),
            state="split",
            now=now,
            config_hash=str(event.get("atomizer_config_hash", "")),
            requeued_from="parked",
            requeue_reason=reason,
        )
        if not ok:
            # 併發 writer 在快照後改了狀態（refusal=目前狀態），或 stale `now`
            # 不會贏得 fold（refusal="stale-timestamp"）：拒絕而非復活已 promote
            # 的 session、或回報未實際生效的 split。
            skipped.append({"session_key": key, "reason": refusal})
            continue
        requeued.append(
            {
                "session_key": key,
                "previous_failure_category": str(event.get("failure_category", "")),
                "fragments": fragments,
            }
        )
    return {"requeued": requeued, "skipped": skipped}
