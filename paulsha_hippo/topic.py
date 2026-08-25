"""同主題判定（fix 2）：純函式，不讀檔、不 import LLM 相依。"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Mapping

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_STOP = {"and", "are", "for", "from", "into", "one", "only", "that", "the", "their", "then", "this", "use", "with"}
MIN_TOKENS_FOR_JACCARD = 4
JACCARD_THRESHOLD = 0.6


def canonical_title(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def strip_project_prefix(title: str, project: str) -> str:
    if project and title.casefold().startswith(project.casefold()):
        return title[len(project):].lstrip(" :-—").strip()
    return title


def title_tokens(title: str, project: str = "") -> set[str]:
    text = strip_project_prefix(str(title or ""), project or "")
    units = {t.casefold() for t in _WORD_RE.findall(text) if t.casefold() not in _STOP}
    for run in _CJK_RE.findall(text):
        units.update(run[i:i + 2] for i in range(len(run) - 1))
    return units


def family_key(project: str, families: Iterable[Iterable[str]] = ()) -> str:
    for fam in families:
        members = sorted(str(m) for m in fam)
        if project in members:
            return members[0]
    return project


def _canon_set(note: Mapping) -> set[str]:
    names = [note.get("title")] + list(note.get("aliases") or [])
    return {canonical_title(n) for n in names if canonical_title(n)}


def is_same_topic(a: Mapping, b: Mapping, *, families: Iterable[Iterable[str]] = ()) -> bool:
    if family_key(str(a.get("project", "")), families) != family_key(str(b.get("project", "")), families):
        return False
    if _canon_set(a) & _canon_set(b):
        return True
    ta = title_tokens(str(a.get("title", "")), str(a.get("project", "")))
    tb = title_tokens(str(b.get("title", "")), str(b.get("project", "")))
    if len(ta) < MIN_TOKENS_FOR_JACCARD or len(tb) < MIN_TOKENS_FOR_JACCARD:
        return False
    union = ta | tb
    return bool(union) and len(ta & tb) / len(union) >= JACCARD_THRESHOLD


def _recency_key(value: object) -> datetime:
    """把 captured_at 解析成可比較的 datetime；缺或不可解析視為最舊（datetime.min）。

    只用 stdlib `datetime.fromisoformat`；額外容忍尾隨 `Z`（fromisoformat 在
    3.11 之前不接受）。aware 值換算為 UTC 後去 tzinfo，naive 值原樣使用，
    確保同一批排序 key 型別一致、彼此可比較，不會因 naive/aware 混用丟例外。
    """
    text = str(value or "").strip()
    if not text:
        return datetime.min
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def collapse_same_topic(hits: list[dict], *, families: Iterable[Iterable[str]] = ()) -> tuple[list[dict], dict[str, list[str]]]:
    ordered = sorted(hits, key=lambda h: _recency_key(h.get("captured_at")), reverse=True)
    kept: list[dict] = []
    collapsed: dict[str, list[str]] = {}
    for h in ordered:
        owner = next((k for k in kept if is_same_topic(k, h, families=families)), None)
        if owner is None:
            kept.append(h)
        else:
            collapsed.setdefault(str(owner["slice_id"]), []).append(str(h["slice_id"]))
    return kept, collapsed
