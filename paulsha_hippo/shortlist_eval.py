"""Push shortlist BM25 基準線評估（#158，#148 H1）。

讀取凍結 query 集（每個 query 附 BM25 top-12 候選、分數、相關性標註、每列字元數；
格式見 docs/push-shadow-baseline.md），比較兩個選取策略：

  - baseline：現行 push——依凍結排序取前 ``BASELINE_K``（=3）則；
  - narrowed：在 baseline 內以 ``push_shadow.narrowed_indices`` 收窄（``-bm25 >=
    bm25_min_score``、最多 ``max_k`` 則）——與線上 shadow 共用同一條規則。

輸出 Precision@3、Noise@3、Relevant-missed@12 與注入字元量。全部為純函式、整數累加後
才除、固定 4 位小數，對同一輸入逐位元可重現；不讀 runtime config、不碰 memory root、
不呼叫任何 LLM。``freeze_queries`` 另提供「由既有索引產生待標註骨架」的唯讀輔助。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .push_shadow import DEFAULT_MAX_K, DEFAULT_MIN_SCORE, MAX_K_LIMIT, narrowed_indices, relevance_score

FROZEN_FORMAT = "hippo-shortlist-frozen-queries"
FROZEN_VERSION = 1
REPORT_FORMAT = "hippo-shortlist-eval"
REPORT_VERSION = 1
BASELINE_K = 3   # = hooks._shortlist_common.SHORTLIST_K（現行 push 每次最多 3 則）
FETCH_K = 12     # = hooks._shortlist_common.SHORTLIST_FETCH_K（BM25 過取候選數）
FREEZE_PLACEHOLDER_SESSION = "00000000-0000-0000-0000-000000000000"
_DECIMALS = 4

__all__ = [
    "BASELINE_K", "DEFAULT_MAX_K", "DEFAULT_MIN_SCORE", "FETCH_K", "FREEZE_PLACEHOLDER_SESSION",
    "FROZEN_FORMAT", "FROZEN_VERSION", "MAX_K_LIMIT", "REPORT_FORMAT", "REPORT_VERSION",
    "Candidate", "FrozenQuery", "FrozenSet", "FrozenSetError", "auto_thresholds", "evaluate",
    "freeze_queries", "load_frozen_set", "render_text", "report", "sweep",
]


class FrozenSetError(ValueError):
    """凍結 query 集不可讀或不符格式。"""


@dataclass(frozen=True)
class Candidate:
    note_id: str
    bm25: float
    relevant: bool
    chars: int


@dataclass(frozen=True)
class FrozenQuery:
    id: str
    query: str
    candidates: tuple[Candidate, ...]
    annotator: str
    method: str


@dataclass(frozen=True)
class FrozenSet:
    name: str
    sha256: str
    block_overhead_chars: int
    queries: tuple[FrozenQuery, ...]


# ---------------------------------------------------------------------------
# 載入與驗證
# ---------------------------------------------------------------------------

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _parse_candidate(raw: Any, where: str) -> Candidate:
    if not isinstance(raw, Mapping):
        raise FrozenSetError(f"{where}: candidate must be an object")
    note_id = raw.get("note_id")
    if not _nonempty_str(note_id):
        raise FrozenSetError(f"{where}: note_id must be a non-empty string")
    bm25 = raw.get("bm25")
    if relevance_score(bm25) is None:
        raise FrozenSetError(f"{where}: bm25 must be a finite number (raw FTS5 bm25, lower = better)")
    if "relevant" not in raw or raw.get("relevant") is None:
        raise FrozenSetError(f"{where}: candidate {note_id} is unlabeled (relevant must be true/false)")
    relevant = raw.get("relevant")
    if not isinstance(relevant, bool):
        raise FrozenSetError(f"{where}: relevant must be true or false")
    chars = raw.get("chars")
    if not _is_int(chars) or chars < 0:
        raise FrozenSetError(f"{where}: chars must be a non-negative integer")
    return Candidate(note_id=note_id, bm25=float(bm25), relevant=relevant, chars=chars)


def _parse_query(raw: Any, index: int, default_annotator: Any, default_method: Any) -> FrozenQuery:
    where = f"queries[{index}]"
    if not isinstance(raw, Mapping):
        raise FrozenSetError(f"{where}: must be an object")
    qid = raw.get("id")
    if not _nonempty_str(qid):
        raise FrozenSetError(f"{where}: id must be a non-empty string")
    where = f"query {qid}"
    query = raw.get("query")
    if not _nonempty_str(query):
        raise FrozenSetError(f"{where}: query must be a non-empty string")
    raw_candidates = raw.get("candidates")
    if not isinstance(raw_candidates, list):
        raise FrozenSetError(f"{where}: candidates must be a list")
    if len(raw_candidates) > FETCH_K:
        raise FrozenSetError(f"{where}: at most {FETCH_K} candidates (BM25 top-{FETCH_K})")
    candidates = tuple(_parse_candidate(c, f"{where} candidates[{i}]")
                       for i, c in enumerate(raw_candidates))
    seen: set[str] = set()
    for cand in candidates:
        if cand.note_id in seen:
            raise FrozenSetError(f"{where}: duplicate note_id {cand.note_id}")
        seen.add(cand.note_id)
    annotator = raw.get("annotator", default_annotator)
    method = raw.get("method", default_method)
    if not _nonempty_str(annotator):
        raise FrozenSetError(f"{where}: annotator missing (set top-level or per-query annotator)")
    if not _nonempty_str(method):
        raise FrozenSetError(f"{where}: method missing (set top-level or per-query method)")
    return FrozenQuery(id=qid, query=query, candidates=candidates,
                       annotator=annotator.strip(), method=method.strip())


def parse_frozen_set(data: Any, *, sha256: str = "") -> FrozenSet:
    if not isinstance(data, Mapping):
        raise FrozenSetError("frozen set must be a JSON object")
    if data.get("format") != FROZEN_FORMAT:
        raise FrozenSetError(f"format must be {FROZEN_FORMAT!r}")
    if data.get("version") != FROZEN_VERSION:
        raise FrozenSetError(f"unsupported version {data.get('version')!r} (expected {FROZEN_VERSION})")
    overhead = data.get("block_overhead_chars", 0)
    if not _is_int(overhead) or overhead < 0:
        raise FrozenSetError("block_overhead_chars must be a non-negative integer")
    raw_queries = data.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise FrozenSetError("queries must be a non-empty list")
    queries = tuple(_parse_query(q, i, data.get("annotator"), data.get("method"))
                    for i, q in enumerate(raw_queries))
    seen: set[str] = set()
    for q in queries:
        if q.id in seen:
            raise FrozenSetError(f"duplicate query id {q.id}")
        seen.add(q.id)
    name = data.get("name")
    return FrozenSet(name=name if _nonempty_str(name) else "(unnamed)", sha256=sha256,
                     block_overhead_chars=overhead, queries=queries)


def load_frozen_set(path: Path) -> FrozenSet:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise FrozenSetError(f"cannot read frozen set {path}: {exc}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrozenSetError(f"frozen set is not valid UTF-8 JSON: {exc}") from None
    return parse_frozen_set(data, sha256=hashlib.sha256(raw).hexdigest())


# ---------------------------------------------------------------------------
# 選取與指標
# ---------------------------------------------------------------------------

def _baseline(q: FrozenQuery) -> list[Candidate]:
    return list(q.candidates[:BASELINE_K])


def _narrowed(q: FrozenQuery, *, min_score: float, max_k: int) -> list[Candidate]:
    base = _baseline(q)
    keep = narrowed_indices([c.bm25 for c in base], min_score=min_score, max_k=max_k)
    return [base[i] for i in keep]


def _chars(selected: Sequence[Candidate], overhead: int) -> int:
    return overhead + sum(c.chars for c in selected) if selected else 0


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, _DECIMALS) if den else None


def _metrics(fs: FrozenSet, selections: Sequence[Sequence[Candidate]]) -> dict[str, Any]:
    injected = relevant_injected = relevant_top = missed = empty = chars = 0
    for q, sel in zip(fs.queries, selections):
        chosen = {c.note_id for c in sel}
        injected += len(sel)
        relevant_injected += sum(1 for c in sel if c.relevant)
        rel = [c for c in q.candidates if c.relevant]
        relevant_top += len(rel)
        missed += sum(1 for c in rel if c.note_id not in chosen)
        empty += 0 if sel else 1
        chars += _chars(sel, fs.block_overhead_chars)
    n = len(fs.queries)
    noise = injected - relevant_injected
    return {
        "queries": n,
        "injected_notes": injected,
        "relevant_injected": relevant_injected,
        "noise_injected": noise,
        "relevant_in_top12": relevant_top,
        "relevant_missed": missed,
        "empty_injections": empty,
        "injected_chars": chars,
        # Precision@3：被注入（≤3 則）者中相關的比例（micro）；沒有任何注入時為 null。
        "precision_at_3": _ratio(relevant_injected, injected),
        # Noise@3：平均每個 query 注入幾則不相關 note。
        "noise_at_3": _ratio(noise, n),
        # Relevant-missed@12：top-12 內相關 note 未被注入的比例；沒有任何相關時為 null。
        "relevant_missed_at_12": _ratio(missed, relevant_top),
        "injected_chars_mean": _ratio(chars, n),
    }


def evaluate(fs: FrozenSet, *, min_score: float, max_k: int) -> dict[str, Any]:
    base = [_baseline(q) for q in fs.queries]
    narrow = [_narrowed(q, min_score=min_score, max_k=max_k) for q in fs.queries]
    baseline_m = _metrics(fs, base)
    narrowed_m = _metrics(fs, narrow)
    per_query = []
    for q, b, n in zip(fs.queries, base, narrow):
        chosen = {c.note_id for c in n}
        per_query.append({
            "id": q.id,
            "baseline": [c.note_id for c in b],
            "narrowed": [c.note_id for c in n],
            "relevant": [c.note_id for c in q.candidates if c.relevant],
            "relevant_missed": [c.note_id for c in q.candidates if c.relevant and c.note_id not in chosen],
            "baseline_chars": _chars(b, fs.block_overhead_chars),
            "narrowed_chars": _chars(n, fs.block_overhead_chars),
        })
    return {
        "baseline": baseline_m,
        "narrowed": narrowed_m,
        "delta": {key: narrowed_m[key] - baseline_m[key]
                  for key in ("injected_notes", "noise_injected", "relevant_missed", "injected_chars")},
        "per_query": per_query,
    }


def auto_thresholds(fs: FrozenSet) -> list[float]:
    """校準用門檻格點：0 加上 baseline 內所有出現過的 score（=-bm25），排序去重。

    門檻落在兩個相鄰 score 之間時結果與取下一個 score 相同，這個格點因此已涵蓋所有
    可區分的收窄結果，且完全由凍結樣本決定（可重現）。
    """
    values = {0.0}
    for q in fs.queries:
        for c in _baseline(q):
            values.add(round(-c.bm25, 6))
    return sorted(values)


def sweep(fs: FrozenSet, thresholds: Iterable[float], *, max_k: int) -> dict[str, Any]:
    """逐門檻評估；推薦值＝「不比 baseline 多漏任何相關 note」的最大門檻（見 docs 校準段）。"""
    baseline_missed = _metrics(fs, [_baseline(q) for q in fs.queries])["relevant_missed"]
    rows = []
    recommended: float | None = None
    for threshold in sorted({float(t) for t in thresholds}):
        m = _metrics(fs, [_narrowed(q, min_score=threshold, max_k=max_k) for q in fs.queries])
        extra = m["relevant_missed"] - baseline_missed
        rows.append({"bm25_min_score": threshold, **m, "extra_missed": extra})
        if extra <= 0:
            recommended = threshold
    return {
        "max_k": max_k,
        "rule": "largest bm25_min_score with relevant_missed <= baseline (extra_missed == 0)",
        "rows": rows,
        "recommended_bm25_min_score": recommended,
    }


def report(fs: FrozenSet, *, min_score: float, max_k: int,
           thresholds: Iterable[float] | None = None) -> dict[str, Any]:
    result = evaluate(fs, min_score=min_score, max_k=max_k)
    out: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "frozen_set": {
            "name": fs.name,
            "sha256": fs.sha256,
            "queries": len(fs.queries),
            "annotators": sorted({q.annotator for q in fs.queries}),
            "methods": sorted({q.method for q in fs.queries}),
        },
        "params": {"baseline_k": BASELINE_K, "fetch_k": FETCH_K,
                   "bm25_min_score": float(min_score), "max_k": max_k},
        **result,
    }
    if thresholds is not None:
        out["sweep"] = sweep(fs, thresholds, max_k=max_k)
    return out


def _fmt(value: Any, pattern: str = "{:.4f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def _metric_row(label: str, m: Mapping[str, Any], width: int) -> str:
    return (f"{label:<{width}}  {_fmt(m['precision_at_3']):>11}  {_fmt(m['noise_at_3']):>7}  "
            f"{_fmt(m['relevant_missed_at_12']):>18}  {m['injected_chars']:>14}  "
            f"{_fmt(m['injected_chars_mean'], '{:.2f}'):>10}  {m['empty_injections']:>5}")


def render_text(rep: Mapping[str, Any]) -> str:
    fs = rep["frozen_set"]
    params = rep["params"]
    header = (f"{'policy':<10}  {'Precision@3':>11}  {'Noise@3':>7}  {'Relevant-missed@12':>18}  "
              f"{'injected_chars':>14}  {'chars_mean':>10}  {'empty':>5}")
    lines = [
        f"frozen set: {fs['name']} · queries={fs['queries']} · sha256={fs['sha256'][:12]}",
        f"annotators: {', '.join(fs['annotators'])}",
        f"baseline: 依凍結排序取前 {params['baseline_k']} 則（現行 push）",
        f"narrowed: bm25_min_score={params['bm25_min_score']:.4f} max_k={params['max_k']}"
        f"（score = -bm25，只從 baseline 內收窄）",
        "",
        header,
        _metric_row("baseline", rep["baseline"], 10),
        _metric_row("narrowed", rep["narrowed"], 10),
        "",
        "delta (narrowed - baseline): " + " ".join(
            f"{k}={v:+d}" for k, v in sorted(rep["delta"].items())),
    ]
    sw = rep.get("sweep")
    if sw:
        lines += ["", f"sweep (max_k={sw['max_k']}; rule: {sw['rule']})",
                  f"{'min_score':<10}  {'Precision@3':>11}  {'Noise@3':>7}  {'Relevant-missed@12':>18}  "
                  f"{'injected_chars':>14}  {'chars_mean':>10}  {'empty':>5}  {'extra_missed':>12}"]
        for row in sw["rows"]:
            lines.append(_metric_row(f"{row['bm25_min_score']:.4f}", row, 10)
                         + f"  {row['extra_missed']:>12}")
        lines.append(f"recommended_bm25_min_score: {_fmt(sw['recommended_bm25_min_score'])}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# freeze：由既有索引產生待標註骨架（唯讀）
# ---------------------------------------------------------------------------

def freeze_queries(memory_root: Path, project: str, queries: Sequence[str], *, annotator: str,
                   method: str, name: str, tool: str = "claude-code",
                   now: datetime | None = None) -> dict[str, Any]:
    """對每個 query 跑與 prompt hook 相同的 FTS 淨化＋``search()``（top-12），輸出待標註骨架。

    唯讀：不記 offered、不寫 runtime 檔。每個候選的 ``chars`` 為 hook 注入該列時的字元數
    （含換行、經同一 redaction boundary）；``block_overhead_chars`` 為提示行＋applied 指引
    的字元數（以 claude-code 與固定佔位 session id 計，實際 session id 長度不同時只差常數）。
    ``relevant`` 一律為 null，須人工標註後 ``hippo shortlist eval`` 才會接受。
    """
    from . import policy
    from .hooks._shortlist_common import _applied_hint, _summary
    from .hooks._wakeup_common import format_show_command
    from .moc import search as search_mod
    from .retrieval import format_shortlist, to_fts_query
    from .runtime_flags import load_flags

    root = Path(memory_root)
    read_hint = load_flags().read_hint
    show_cmd = format_show_command(root, tool, FREEZE_PLACEHOLDER_SESSION)
    hint_line = _applied_hint(root, tool, FREEZE_PLACEHOLDER_SESSION)
    header = format_shortlist([{"title": "x"}], hint=read_hint, show_command=show_cmd).split("\n")[0]
    overhead = len(header) + 1 + len(hint_line)

    out_queries = []
    for index, text in enumerate(queries, start=1):
        fts = to_fts_query(text)
        hits = (search_mod.search(root, fts, project=project, limit=FETCH_K, include_decayed=False)
                if fts else [])
        for hit in hits:
            hit["summary"] = _summary(hit.get("path", ""), str(hit.get("title") or ""))
        rows = format_shortlist(hits, hint=read_hint, show_command=show_cmd).split("\n")[1:] if hits else []
        if hits:
            block = format_shortlist(hits, hint=read_hint, show_command=show_cmd)
            redacted = policy.check_boundary(
                "external_to_raw", block, project_slug=project or "_unknown",
                session_ref=FREEZE_PLACEHOLDER_SESSION).text.split("\n")
            if len(redacted) == 1 + len(hits):
                rows = redacted[1:]
        candidates = []
        for hit, row in zip(hits, rows):
            candidates.append({
                "note_id": str(hit.get("slice_id")),
                "bm25": round(float(hit.get("score")), 6),
                "relevant": None,
                "chars": 1 + len(row),
                "title": str(hit.get("title") or ""),
                "path": str(hit.get("path") or ""),
            })
        out_queries.append({"id": f"q{index:03d}", "query": text, "fts_query": fts,
                            "candidates": candidates})
    return {
        "format": FROZEN_FORMAT,
        "version": FROZEN_VERSION,
        "name": name,
        "project": project,
        "frozen_at": (now or datetime.now(timezone.utc)).isoformat(),
        "annotator": annotator,
        "method": method,
        "fetch_k": FETCH_K,
        "baseline_k": BASELINE_K,
        "block_overhead_chars": overhead,
        "queries": out_queries,
    }
