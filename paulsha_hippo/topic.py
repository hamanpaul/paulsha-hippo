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


def recency_key(value: object) -> datetime:
    """`_recency_key` 的公開名稱：把 captured_at 解析成可比較的 datetime。

    模組外要比 captured_at 新舊的一律用這個，不要用字串序——真實記憶庫同時混用
    ``...Z``、``...+08:00`` 與 YAML round-trip 後的 ``2026-05-31 00:00:00+00:00``
    三種寫法，字串序在它們之間完全不成立（`atomizer/pipeline.py` 的 publish 路徑
    就曾因此漏掉合法前身）。私名保留給既有呼叫端。
    """
    return _recency_key(value)


def collapse_same_topic(hits: list[dict], *, families: Iterable[Iterable[str]] = ()) -> tuple[list[dict], dict[str, list[str]]]:
    """同主題折疊：recency 只決定「同組誰存活」，`kept` 的輸出順序仍是 `hits` 的相關度順序。

    `hits` 依呼叫端（BM25＋usage boost）已是相關度排序；折疊不得把整份 shortlist 依
    captured_at 重排——否則强相關但非最新的主題會被擠出前 K。做法：先用既有的
    recency-descending 貪婪分組（newest-first，同分時 `sorted` 的 stable 排序保留
    `hits` 原序，決定性地選出每組存活者），再依「該組成員在 hits 中最小的原始索引」
    把 kept 重新排回相關度序——最小索引最小的組排最前，讓一個強相關組即使其最新成員
    在原始命中序中排得靠後，仍保住該組最靠前那個位置的名次。
    """
    orig_index = {str(h["slice_id"]): i for i, h in enumerate(hits)}
    ordered = sorted(hits, key=lambda h: _recency_key(h.get("captured_at")), reverse=True)
    kept: list[dict] = []
    collapsed: dict[str, list[str]] = {}
    group_min_index: dict[str, int] = {}
    for h in ordered:
        sid = str(h["slice_id"])
        owner = next((k for k in kept if is_same_topic(k, h, families=families)), None)
        if owner is None:
            kept.append(h)
            group_min_index[sid] = orig_index[sid]
        else:
            owner_id = str(owner["slice_id"])
            collapsed.setdefault(owner_id, []).append(sid)
            group_min_index[owner_id] = min(group_min_index[owner_id], orig_index[sid])
    kept.sort(key=lambda h: group_min_index[str(h["slice_id"])])
    return kept, collapsed
