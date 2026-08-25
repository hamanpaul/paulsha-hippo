"""Follow-up ledger（fix 5，issue #136）：可行動語句 → runtime/ledger/followups.jsonl；

extract：從 knowledge notes body 逐行抽取可行動語句＋其 `path:line` 引用與預期已過時
值（pure，無 I/O，供 extract_all 呼叫）。append_event/fold：append-only ledger 的
writer／reader（比照 _shortlist_common._append_offered_ledger 的 flush＋fsync 落盤慣例、
ledger/usage.py 的 fold-by-last-event 慣例）。verify：對 fold 後仍 open 的項目唯讀重讀
其引用的 file:line（±window），確認 expected_stale 是否還在——在→ verified-open，
不在→ resolved-in-source（自動關閉），缺檔／缺 root／路徑逃逸／無預期值→ unverifiable。
close：使用者手動關閉一筆。

Task 12（後續）才把這裡接進 dream/wakeup/KPI；本模組獨立可用，不依賴那些迴路。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from paulsha_hippo.atomizer.slice_frontmatter import CITE_RE
from paulsha_hippo.ledger.integrity import ledger_dir
from paulsha_hippo.moc import frontmatter_io as fio

ACTIONABLE_RE = re.compile(r"需要更新|需加|缺口|TODO|should be updated|尚未修|需修正|待補")
STALE_VALUE_RE = re.compile(r"`([^`]{1,64})`|(\d[\d,\.]{2,})")

OPEN_STATES = ("opened", "verified-open")
VALID_EVENTS = ("opened", "verified-open", "resolved-in-source", "closed-manual", "unverifiable")


def ledger_path(root: Path) -> Path:
    return ledger_dir(Path(root)) / "followups.jsonl"


def _fid(slice_id: str, target: dict | None, claim: str) -> str:
    locator = f"{target['path']}:{target['line']}" if target else "-"
    digest = hashlib.sha256(f"{slice_id}|{locator}|{claim}".encode("utf-8")).hexdigest()
    return "fu-" + digest[:16]


def _cite_in(lines: list[str], index: int) -> dict | None:
    """在該行、前一行、後一行（依此順序）找第一個 `path:line` 引用。"""
    for j in (index, index - 1, index + 1):
        if 0 <= j < len(lines):
            match = CITE_RE.search(lines[j])
            if match:
                return {"path": match.group(1), "line": int(match.group(2))}
    return None


def extract_followups(*, slice_id: str, project: str, body: str, cites: list[dict]) -> list[dict]:
    """純函式：逐行掃描 body，可行動語句一行一筆，`cites` 僅在該行找不到引用時作 fallback。"""
    lines = (body or "").splitlines()
    out: list[dict] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or not ACTIONABLE_RE.search(line):
            continue
        target = _cite_in(lines, index)
        if target is None and cites:
            target = dict(cites[0])
        stale_match = STALE_VALUE_RE.search(line)
        expected_stale = (stale_match.group(1) or stale_match.group(2)) if stale_match else None
        out.append({
            "id": _fid(slice_id, target, line),
            "slice_id": slice_id,
            "project": project,
            "target": target,
            "expected_stale": expected_stale,
            "claim": line,
            "source": "regex",
        })
    return out


def append_event(root: Path, event: dict, *, now: str) -> None:
    """Append 一筆事件＋flush＋fsync 落盤（比照 _append_offered_ledger 的 crash-commit-point 慣例）。"""
    if event.get("event") not in VALID_EVENTS:
        raise ValueError(f"invalid followup event: {event.get('event')!r}")
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now, **event}, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def fold(root: Path) -> dict[str, dict]:
    """把 append-only 事件流折成 {id: 目前狀態}；最後一筆事件為 state，opened 時的欄位保留。"""
    state: dict[str, dict] = {}
    try:
        raw = ledger_path(root).read_text(encoding="utf-8")
    except OSError:
        return state
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        fid = event.get("id")
        if not fid:
            continue
        current = state.setdefault(fid, {})
        if event.get("event") == "opened":
            current.update({k: v for k, v in event.items() if k not in ("event", "ts")})
        current["state"] = event.get("event")
        current["updated_at"] = event.get("ts")
    return state


def open_count(root: Path, project: str) -> int:
    return sum(
        1 for state in fold(root).values()
        if state.get("state") in OPEN_STATES and state.get("project") == project
    )


def extract_all(root: Path, *, apply: bool, now: str, project: str | None = None) -> dict:
    """走訪 `<root>/knowledge/**/*.md`（memory_layer == knowledge），抽 follow-up 並（apply 時）開單。

    冪等：同 id 已在 fold() 中（無論何種狀態，含已關閉）即跳過，不重複開單。
    """
    root = Path(root)
    known = fold(root)
    summary = {"scanned": 0, "candidates": 0, "with_target": 0, "opened": 0}
    knowledge = root / "knowledge"
    if not knowledge.is_dir():
        return summary
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fm.get("memory_layer") != "knowledge" or (project and fm.get("project") != project):
            continue
        summary["scanned"] += 1
        cites_raw = fm.get("cites") if isinstance(fm.get("cites"), list) else []
        cites = [c for c in cites_raw if isinstance(c, dict)]
        items = extract_followups(
            slice_id=str(fm.get("slice_id", "")), project=str(fm.get("project", "")),
            body=body, cites=cites,
        )
        for item in items:
            summary["candidates"] += 1
            if item["target"] is not None:
                summary["with_target"] += 1
            if item["id"] in known:
                continue
            if apply:
                append_event(root, {**item, "event": "opened"}, now=now)
                known[item["id"]] = item
                summary["opened"] += 1
    return summary


def _resolve(target: dict, project: str, roots_by_project: dict) -> Path | None:
    """絕對路徑直接檢查存在；相對路徑依序試 roots_by_project[project] 各 root，且結果須落在
    該 root 之內（is_relative_to 圍籬，比照 recovery.py／provenance_backfill.py 的 archive 圍籬
    寫法）——避免 `../../etc/passwd` 之類的引用把唯讀讀取帶出專案根之外。target 若缺
    ``path``／型別不對（壞 ledger 事件）視同解析失敗，回 None，不 raise。
    """
    path_value = target.get("path") if isinstance(target, dict) else None
    if not isinstance(path_value, str) or not path_value:
        return None
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for root_str in roots_by_project.get(project, ()):
        try:
            root_resolved = Path(root_str).resolve()
            resolved = (root_resolved / candidate).resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        if resolved.is_file():
            return resolved
    return None


def _verify_outcome(target: dict, expected_stale: object, resolved_path: Path | None,
                      window: int) -> str:
    """對單一項目判定 verify 結果；純函式、從不 raise——任何缺失/型別問題一律 unverifiable。"""
    if resolved_path is None or not expected_stale:
        return "unverifiable"
    try:
        lines = resolved_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        line_no = int(target["line"])
    except (OSError, KeyError, TypeError, ValueError):
        # 讀檔失敗，或 target 來自 append-only ledger 的自由格式 JSON——手改／外部工具寫入
        # 的壞事件（缺 line／非數字）——都不得讓唯讀 verify 整輪掛掉。
        return "unverifiable"
    lo = max(1, line_no - window)
    hi = min(len(lines), line_no + window)
    if lo > hi:
        # 目標行已完全落在檔案目前長度之外（例如檔案被大幅精簡）——裁切後視窗是空的，
        # 0 行可比對不等於「過時值已不在」，貿然歸 resolved-in-source 會誤將真正的
        # 資料流失偽裝成已修復自動關閉。沒東西可比對就是查無結果。
        return "unverifiable"
    found = any(str(expected_stale) in lines[i - 1] for i in range(lo, hi + 1))
    return "verified-open" if found else "resolved-in-source"


def verify(root: Path, *, roots_by_project: dict, now: str, project: str | None = None,
           window: int = 2) -> dict:
    """對 fold 後狀態為 open（opened／verified-open）且有 target 的項目，唯讀重讀其
    file:line（±window，1-based，越界裁切）判定 expected_stale 是否仍在。從不寫 repo 檔案、
    從不 raise——缺檔／out-of-range／路徑逃逸／無 expected_stale 一律歸 unverifiable。
    """
    summary = {"checked": 0, "verified_open": 0, "resolved": 0, "unverifiable": 0}
    event_to_stat = {
        "verified-open": "verified_open",
        "resolved-in-source": "resolved",
        "unverifiable": "unverifiable",
    }
    for fid, state in fold(root).items():
        if state.get("state") not in OPEN_STATES:
            continue
        target = state.get("target")
        if not target or not isinstance(target, dict):
            continue
        item_project = str(state.get("project", ""))
        if project and item_project != project:
            continue
        summary["checked"] += 1
        resolved_path = _resolve(target, item_project, roots_by_project)
        outcome = _verify_outcome(target, state.get("expected_stale"), resolved_path, window)
        append_event(
            root, {"id": fid, "event": outcome,
                   "detail": {"path": str(resolved_path) if resolved_path else None}},
            now=now,
        )
        summary[event_to_stat[outcome]] += 1
    return summary


def close(root: Path, fid: str, *, reason: str, now: str) -> bool:
    if fid not in fold(root):
        return False
    append_event(root, {"id": fid, "event": "closed-manual", "detail": {"reason": reason}}, now=now)
    return True
