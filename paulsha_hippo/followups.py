"""Follow-up ledger（fix 5，issue #136）：可行動語句 → runtime/ledger/followups.jsonl；

extract：從 knowledge notes body 逐行抽取可行動語句＋其 `path:line` 引用與預期已過時
值（pure，無 I/O，供 extract_all 呼叫）。append_event/fold：append-only ledger 的
writer／reader（比照 _shortlist_common._append_offered_ledger 的 flush＋fsync 落盤慣例、
ledger/processing.py 的 fold-by-last-event 慣例：事件依 (ts, original_index) 排序後折疊，
較晚的 ts 勝出，即使該事件在檔案中被較早 append；缺／無法解析的 ts 排最前，視為最舊）。
verify：對 fold 後仍 open 的項目唯讀重讀其引用的 file:line（±window），確認 expected_stale
是否還在——在→ verified-open，不在→ resolved-in-source（自動關閉），缺檔／缺 root／路徑
逃逸／無預期值／行號越界→ unverifiable（並在 detail.reason 標明原因）。close：使用者手動
關閉一筆。

Task 12（後續）才把這裡接進 dream/wakeup/KPI；本模組獨立可用，不依賴那些迴路。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from paulsha_hippo.atomizer.slice_frontmatter import CITE_RE
from paulsha_hippo.ledger.integrity import ledger_dir
from paulsha_hippo.moc import frontmatter_io as fio

ACTIONABLE_RE = re.compile(
    r"需要更新|需加|缺口|TODO|should be updated|尚未修|需修正|待補", re.IGNORECASE
)
STALE_VALUE_RE = re.compile(r"`([^`]{1,64})`|(\d[\d,\.]{2,})")
_BACKTICK_VALUE_RE = re.compile(r"`([^`]{1,64})`")
_BARE_DIGIT_RE = re.compile(r"\d[\d,\.]{2,}")
# cite locator 通常寫成 `` `path:line` ``（反引號包住整個 cite）——把整個 backtick+cite+backtick
# 一起挖掉，而不是只挖 CITE_RE 命中的內文，否則兩側殘留的反引號會跟後面真正的過時值反引號
# 湊成一組偽 span，把中間的無關文字誤當成 expected_stale。
_CITE_BACKTICKED_RE = re.compile(r"`" + CITE_RE.pattern + r"`")
_FENCE_MARKERS = ("```", "~~~")

OPEN_STATES = ("opened", "verified-open")
VALID_EVENTS = ("opened", "verified-open", "resolved-in-source", "closed-manual", "unverifiable")


def ledger_path(root: Path) -> Path:
    return ledger_dir(Path(root)) / "followups.jsonl"


def _fid(slice_id: str, target: dict | None, claim: str) -> str:
    locator = f"{target['path']}:{target['line']}" if target else "-"
    digest = hashlib.sha256(f"{slice_id}|{locator}|{claim}".encode("utf-8")).hexdigest()
    return "fu-" + digest[:16]


def _cite_in(lines: list[str], index: int, fenced: list[bool] | None = None) -> dict | None:
    """在該行、前一行、後一行（依此順序）找第一個 `path:line` 引用；`fenced`（若給）落在
    圍籬內（含 marker 行本身）的相鄰行一律不取（T11 leftover minor：原本沒有尊重
    `_fence_mask`，圍籬開頭 marker 行若剛好長得像 cite locator——例如語言註記寫成
    ```path.py:42``——會被誤當成真正的引用；未閉合圍籬的開頭 marker 行同樣要被排除）。
    """
    for j in (index, index - 1, index + 1):
        if 0 <= j < len(lines) and not (fenced is not None and fenced[j]):
            match = CITE_RE.search(lines[j])
            if match:
                return {"path": match.group(1), "line": int(match.group(2))}
    return None


def _expected_stale(line: str) -> str | None:
    """在 `line` 上找 expected_stale：先排除 `CITE_RE` 在這一行命中的 span（cite locator
    自己的 `path:line` 不該被誤當成過時值），再優先取剩餘文字中第一個反引號值；沒有反引號
    值才退回第一個裸數字 run。挑到的值若整段都是空白，視同沒有 expected_stale（review round 1
    finding 1：原本的 `STALE_VALUE_RE.search(line)` 只取整行最左邊的那個 match，可能命中
    cite locator 自己的反引號 span，或一個較早出現、無關的裸數字）。
    """
    masked = _CITE_BACKTICKED_RE.sub("", line)
    masked = CITE_RE.sub("", masked)
    backtick = _BACKTICK_VALUE_RE.search(masked)
    if backtick:
        candidate = backtick.group(1)
    else:
        digits = _BARE_DIGIT_RE.search(masked)
        candidate = digits.group(0) if digits else None
    if candidate is not None and not candidate.strip():
        return None
    return candidate


def _fence_mask(lines: list[str]) -> list[bool]:
    """回傳每一行是否落在 fenced code block（```／~~~）內；圍籬 marker 行本身也視為圍籬內
    （review round 1 finding 4：TODO 等可行動語句若寫在範例程式碼裡，不該被當成真的待辦）。
    """
    mask: list[bool] = []
    inside = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith(_FENCE_MARKERS):
            mask.append(True)
            inside = not inside
            continue
        mask.append(inside)
    return mask


def extract_followups(*, slice_id: str, project: str, body: str, cites: list[dict]) -> list[dict]:
    """純函式：逐行掃描 body，可行動語句一行一筆，`cites` 僅在該行找不到引用時作 fallback。
    大小寫不敏感（`Should be updated`／`todo:` 都算），fenced code block 內的行一律跳過。
    """
    lines = (body or "").splitlines()
    fenced = _fence_mask(lines)
    out: list[dict] = []
    for index, raw in enumerate(lines):
        if fenced[index]:
            continue
        line = raw.strip()
        if not line or not ACTIONABLE_RE.search(line):
            continue
        target = _cite_in(lines, index, fenced)
        if target is None and cites:
            target = dict(cites[0])
        expected_stale = _expected_stale(line)
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


def _warn(msg: str) -> None:
    """一行 stderr warning（比照 cli.py `warning: ...` 慣例）；不 raise，呼叫端的 fail-closed
    分支靠這個把「這筆為什麼被跳過」講清楚，而不是靜默吞掉。
    """
    print(f"warning: followups: {msg}", file=sys.stderr)


def _redact_followup_item(item: dict, *, project: str, session_ref: str) -> dict:
    """把即將寫入 ledger 的 `claim`／`expected_stale` 經 `policy.check_boundary` 遮蔽後回傳
    新 item（issue #136 fix 11b：follow-up ledger 是 memory-consumer——Task 12 的 wakeup
    brief 會把這裡存的文字秀給 agent，之前完全沒經過 boundary check 就落 ledger）。

    比照 `hooks/_shortlist_common._redact` 的呼叫慣例：`project_slug` 用 note 自身
    project（缺時 `_unknown`），`session_ref` 用 note 的 slice_id。任何例外（policy 載入
    失敗、check_boundary 本身炸掉…）一律原樣往上拋，讓呼叫端 fail-closed 整筆跳過、不落
    ledger——見 `extract_all`。
    """
    from paulsha_hippo import policy

    slug = project or "_unknown"
    claim = policy.check_boundary(
        "external_to_raw", item["claim"], project_slug=slug, session_ref=session_ref,
    ).text
    expected_stale = item["expected_stale"]
    if expected_stale is not None:
        expected_stale = policy.check_boundary(
            "external_to_raw", str(expected_stale), project_slug=slug, session_ref=session_ref,
        ).text
    return {**item, "claim": claim, "expected_stale": expected_stale}


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
    """把 append-only 事件流依 (ts, original_index) 排序後折成 {id: 目前狀態}。

    較晚的 ts 勝出，即使該事件在檔案中被較早 append；ts 相同時後 append 者（index 較大）
    勝出；缺／無法解析的 ts 一律排最前（視為最舊）——比照 `ledger/processing.py` 的
    `_fold_indexed`／`fold_events`（sort by (ts, original_index) 後讓後處理者覆寫勝出），
    而非單純依 append 順序覆寫（review round 1 finding 2）。`opened` 事件的欄位（target／
    expected_stale／claim…）保留為基底紀錄；每筆事件的 `detail`（若有）也會覆寫進狀態，
    讓最新一次 verify／close 的原因可被 `hippo followups list` 讀到。
    """
    state: dict[str, dict] = {}
    try:
        raw = ledger_path(root).read_text(encoding="utf-8")
    except OSError:
        return state
    events: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or not event.get("id"):
            continue
        events.append(event)
    indexed = [(event, idx) for idx, event in enumerate(events)]
    indexed.sort(key=lambda pair: (pair[0].get("ts") or "", pair[1]))
    for event, _ in indexed:
        fid = event["id"]
        current = state.setdefault(fid, {})
        if event.get("event") == "opened":
            current.update({k: v for k, v in event.items() if k not in ("event", "ts")})
        current["state"] = event.get("event")
        current["updated_at"] = event.get("ts")
        if "detail" in event:
            current["detail"] = event["detail"]
    return state


def open_count(root: Path, project: str) -> int:
    return sum(
        1 for state in fold(root).values()
        if state.get("state") in OPEN_STATES and state.get("project") == project
    )


def _followups_enabled_default() -> bool:
    """Best-effort 讀 canonical config 的 `followups.enabled`；任何失敗（含 import 失敗）
    一律回退為啟用，絕不 raise（review round 1 finding 3：flag 要內建進 extract_all 本身，
    不能只在 CLI 擋一次——CLI 之外呼叫 extract_all(apply=True) 時同樣必須受這個 gate 保護）。
    """
    try:
        from paulsha_hippo.runtime_flags import load_flags
        return bool(load_flags().followups_enabled)
    except Exception:
        return True


def extract_all(root: Path, *, apply: bool, now: str, project: str | None = None,
                 enabled: bool | None = None) -> dict:
    """走訪 `<root>/knowledge` 目錄下的 `*.md`（`memory_layer == knowledge`），抽 follow-up 並（apply
    時）開單。

    冪等：同 id 已在 fold() 中（無論何種狀態，含已關閉）即跳過，不重複開單。`enabled` 為
    None 時 best-effort 讀 `runtime_flags.load_flags().followups_enabled`；解析後若停用且
    `apply=True`，一律不落 ledger（等同 dry-run），summary 多帶一個 `"skipped":
    "followups.disabled"` 讓呼叫端知道為什麼沒開單。

    落 ledger 前先經 `_redact_followup_item`（`policy.check_boundary("external_to_raw", ...)`）
    遮蔽 `claim`／`expected_stale`——這兩個欄位會被 Task 12 的 wakeup brief 秀給 agent，是
    memory-consumer（issue #136 fix 11b）。Fail-closed：check_boundary 炸掉的那一筆整筆不落
    ledger，summary 多帶 `"skipped_redaction"` 計數，並印一行 stderr warning；不影響其他筆、
    不 raise。
    """
    root = Path(root)
    if enabled is None:
        enabled = _followups_enabled_default()
    effective_apply = bool(apply) and enabled
    known = fold(root)
    summary = {"scanned": 0, "candidates": 0, "with_target": 0, "opened": 0}
    if apply and not enabled:
        summary["skipped"] = "followups.disabled"
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
            if effective_apply:
                try:
                    item = _redact_followup_item(
                        item, project=str(fm.get("project", "")),
                        session_ref=str(fm.get("slice_id", "")),
                    )
                except Exception as exc:
                    summary["skipped_redaction"] = summary.get("skipped_redaction", 0) + 1
                    _warn(f"boundary check 失敗，略過此筆（不落 ledger）id={item['id']}: {exc}")
                    continue
                append_event(root, {**item, "event": "opened"}, now=now)
                known[item["id"]] = item
                summary["opened"] += 1
    return summary


def _resolve(target: dict, project: str, roots_by_project: dict) -> tuple[Path | None, str | None]:
    """解析 target 對應的實際檔案路徑；回傳 `(path, reason)`——找到時 `reason` 為 None，
    否則為 `"missing-file"`／`"path-escape"`／`"no-root"` 之一（review round 1 finding 5：
    每個 unverifiable 成因要有獨立 reason，供 `hippo followups list` 顯示）。

    絕對路徑與相對路徑套用同一套 root 圍籬：都必須落在 `roots_by_project[project]` 其中
    一個 root 之內（`is_relative_to`）才算數，比照 recovery.py／provenance_backfill.py 的
    archive 圍籬寫法（review round 1 finding 6：絕對路徑 cite 先前只檢查 `is_file()`，
    完全繞過這道圍籬，可讀出專案根之外任意存在的檔案）。project 沒有任何 configured root
    時一律 `"no-root"`——沒有 root 可比對，寧可 unverifiable 也不能放行。target 缺
    ``path``／型別不對（壞 ledger 事件）視同解析失敗，回 `(None, "missing-file")`，不 raise。
    帶 NUL byte 的 path（手改／外部工具寫入的壞事件）在 `Path.resolve()` 丟的是
    `ValueError` 不是 `OSError`——T11 leftover minor，一併接住，同樣歸 unverifiable。
    """
    path_value = target.get("path") if isinstance(target, dict) else None
    if not isinstance(path_value, str) or not path_value:
        return None, "missing-file"
    candidate = Path(path_value)
    roots = roots_by_project.get(project, ())
    if not roots:
        return None, "no-root"
    contained_in_any = False
    for root_str in roots:
        try:
            root_resolved = Path(root_str).resolve()
            resolved = candidate.resolve() if candidate.is_absolute() else (root_resolved / candidate).resolve()
        except (OSError, ValueError):
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        contained_in_any = True
        if resolved.is_file():
            return resolved, None
    return None, ("missing-file" if contained_in_any else "path-escape")


def _verify_outcome(target: dict, expected_stale: object, resolved_path: Path | None,
                     resolve_reason: str | None, window: int) -> tuple[str, str | None]:
    """對單一項目判定 verify 結果；純函式、從不 raise——任何缺失/型別問題一律 unverifiable，
    並帶上區分成因的 `reason`（missing-file／path-escape／no-root／no-expected-stale／
    line-out-of-range）。
    """
    if resolved_path is None:
        return "unverifiable", (resolve_reason or "missing-file")
    if not expected_stale:
        return "unverifiable", "no-expected-stale"
    try:
        lines = resolved_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        # resolve 當下存在，讀取瞬間卻失敗（權限／race）——歸類同缺檔。
        return "unverifiable", "missing-file"
    try:
        line_no = int(target["line"])
    except (KeyError, TypeError, ValueError):
        # target 來自 append-only ledger 的自由格式 JSON——手改／外部工具寫入的壞事件
        # （缺 line／非數字）不得讓唯讀 verify 整輪掛掉，行號本身視同越界。
        return "unverifiable", "line-out-of-range"
    lo = max(1, line_no - window)
    hi = min(len(lines), line_no + window)
    if lo > hi:
        # 目標行已完全落在檔案目前長度之外（例如檔案被大幅精簡）——裁切後視窗是空的，
        # 0 行可比對不等於「過時值已不在」，貿然歸 resolved-in-source 會誤將真正的
        # 資料流失偽裝成已修復自動關閉。
        return "unverifiable", "line-out-of-range"
    found = any(str(expected_stale) in lines[i - 1] for i in range(lo, hi + 1))
    return ("verified-open", None) if found else ("resolved-in-source", None)


def verify(root: Path, *, roots_by_project: dict, now: str, project: str | None = None,
           window: int = 2) -> dict:
    """對 fold 後狀態為 open（opened／verified-open）且有 target 的項目，唯讀重讀其
    file:line（±window，1-based，越界裁切）判定 expected_stale 是否仍在。從不寫 repo 檔案、
    從不 raise——缺檔／out-of-range／路徑逃逸／無 expected_stale 一律歸 unverifiable，
    並在該筆 ledger 事件的 `detail.reason` 記下區分成因。
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
        resolved_path, resolve_reason = _resolve(target, item_project, roots_by_project)
        outcome, reason = _verify_outcome(
            target, state.get("expected_stale"), resolved_path, resolve_reason, window)
        detail = {"path": str(resolved_path) if resolved_path else None}
        if reason:
            detail["reason"] = reason
        append_event(root, {"id": fid, "event": outcome, "detail": detail}, now=now)
        summary[event_to_stat[outcome]] += 1
    return summary


def close(root: Path, fid: str, *, reason: str, now: str) -> bool:
    if fid not in fold(root):
        return False
    append_event(root, {"id": fid, "event": "closed-manual", "detail": {"reason": reason}}, now=now)
    return True
