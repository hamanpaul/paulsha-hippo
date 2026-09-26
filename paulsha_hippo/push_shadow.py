"""Push shortlist 確定性收窄 shadow 量測（#158，#148 H1）。

prompt-time hook 照舊注入現行 shortlist；shadow 開啟時，另外以「BM25 分數門檻＋最多
0–3 則」從**同一批已注入的 claim** 收窄出一份假想 shortlist，連同收窄前後的 note id
與注入字元量寫進獨立的 shadow ledger（``runtime/ledger/push_shadow.jsonl``）。

設計約束（驗收條件）：
  - 預設關閉（``shortlist.push_shadow.enabled: false``）。開啟後注入內容與關閉時逐位元
    相同：shadow 只讀現行管線已算好的 hits／claim／block，不改動任何一個。
  - 純確定性計算：不重跑檢索、不呼叫外部 LLM、不 spawn 子行程、不開網路。
  - best-effort：任何失敗只記 hooks.log，不影響注入；計算超過 ``time_budget_ms``
    即丟棄該筆紀錄（不寫檔），寫入為單次 O_APPEND、無鎖、無 fsync。
  - 不記 prompt／query 原文，只記 FTS query 的 sha256 與 token 數（隱私：shadow
    ledger 不經 redaction boundary）。

分數語意：SQLite FTS5 ``bm25()`` 越負越相關；本模組一律以 ``score = -bm25``
（越大越相關）與門檻 ``bm25_min_score`` 比較。凍結樣本評估（``shortlist_eval``）
共用同一個 ``narrowed_indices``，線上 shadow 與離線評估的收窄規則因此只有一份。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

LEDGER_NAME = "push_shadow.jsonl"
SCHEMA_VERSION = 1

# 預設值說明見 docs/push-shadow-baseline.md「門檻校準」：
#   - bm25_min_score 0.0＝不設分數門檻。FTS5 bm25 的絕對值隨 query token 數與語料
#     規模浮動，沒有凍結樣本前不猜數字；門檻一律由 `hippo shortlist eval --sweep`
#     在凍結樣本上校準後再寫進設定。
#   - max_k 1＝最小的非零推送量，讓未校準前的 shadow 也能量到「只推最相關一則」
#     的字元量差距；每筆事件都記下 before 的 bm25，離線可重算任何 (門檻, max_k)。
DEFAULT_MIN_SCORE = 0.0
DEFAULT_MAX_K = 1
MAX_K_LIMIT = 3  # 收窄只從現行 claim（SHORTLIST_K=3）內取，上限即 3
DEFAULT_TIME_BUDGET_MS = 50.0
MAX_TIME_BUDGET_MS = 1000.0

# 測試可替換的單調時鐘（秒）。呼叫端一律以 `push_shadow.clock()` 取值。
clock: Callable[[], float] = time.perf_counter


@dataclass(frozen=True)
class PushShadowConfig:
    enabled: bool = False
    bm25_min_score: float = DEFAULT_MIN_SCORE
    max_k: int = DEFAULT_MAX_K
    time_budget_ms: float = DEFAULT_TIME_BUDGET_MS


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_config(raw: Any) -> PushShadowConfig:
    """解析 ``shortlist.push_shadow`` 映射；逐鍵驗證，錯型／越界一律退回該鍵預設。永不 raise。"""
    default = PushShadowConfig()
    if not isinstance(raw, Mapping):
        return default
    enabled = raw.get("enabled")
    score = raw.get("bm25_min_score")
    max_k = raw.get("max_k")
    budget = raw.get("time_budget_ms")
    return PushShadowConfig(
        enabled=enabled if isinstance(enabled, bool) else default.enabled,
        bm25_min_score=float(score) if _is_number(score) and score >= 0 else default.bm25_min_score,
        max_k=(max_k if isinstance(max_k, int) and not isinstance(max_k, bool)
               and 0 <= max_k <= MAX_K_LIMIT else default.max_k),
        time_budget_ms=(float(budget) if _is_number(budget) and 0 < budget <= MAX_TIME_BUDGET_MS
                        else default.time_budget_ms),
    )


def relevance_score(bm25: Any) -> float | None:
    """FTS5 bm25（越負越相關）→ score = -bm25（越大越相關）；非有限數值回 None。"""
    if not _is_number(bm25):
        return None
    return -float(bm25)


def narrowed_indices(bm25_scores: Sequence[Any], *, min_score: float, max_k: int) -> list[int]:
    """確定性收窄：依原排序逐筆保留 ``-bm25 >= min_score`` 者，最多 ``max_k`` 則。

    不重排——現行排序（bm25＋link_weight＋usage boost）不保證 bm25 單調，收窄只做
    過濾與截斷，不改 recall／retrieval 的排序演算法。分數缺漏或非數值一律不保留。
    """
    kept: list[int] = []
    for index, bm25 in enumerate(bm25_scores):
        if len(kept) >= max_k:
            break
        score = relevance_score(bm25)
        if score is not None and score >= min_score:
            kept.append(index)
    return kept


def ledger_path(root: Path) -> Path:
    return Path(root) / "runtime" / "ledger" / LEDGER_NAME


def _chars_for(block: str, hint: str, n_rows: int, keep: Sequence[int],
               render: Callable[[list[int]], str] | None) -> tuple[int, bool]:
    """收窄後的注入字元量。回傳 (字元數, 是否精確)。

    注入＝`block + "\\n" + hint`，block 為「提示行＋每則一列」且 redaction 逐行替換、
    不改行數，因此可直接從已 redact 的 block 取提示行與被保留的列，得到與現行管線
    「只注入這幾則」時逐字元相同的長度，不需再跑一次 redaction。若 block 行數與
    claim 對不上（例如標題內含換行），退回以未 redact 的重新排版估算並標為不精確。
    """
    if not keep:
        return 0, True
    lines = block.split("\n")
    if len(lines) == 1 + n_rows:
        rows = sum(1 + len(lines[1 + i]) for i in keep)
        return len(lines[0]) + rows + 1 + len(hint), True
    if render is None:
        raise ValueError("block rows do not align with claim and no fallback renderer")
    return len(render(list(keep))) + 1 + len(hint), False


def build_event(*, tool: str, session_id: str, project: str, query: str,
                hits: Sequence[Mapping[str, Any]], claim: Sequence[Mapping[str, Any]],
                block: str, hint: str, injected: str, config: PushShadowConfig,
                pipeline_ms: float, render: Callable[[list[int]], str] | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """由現行管線已算好的產物組 shadow 事件（純函式，不做 IO）。"""
    rank_of: dict[str, int] = {}
    for index, hit in enumerate(hits):
        sid = hit.get("slice_id")
        if sid and sid not in rank_of:
            rank_of[str(sid)] = index
    before_ids = [str(h.get("slice_id")) for h in claim]
    keep = narrowed_indices([h.get("score") for h in claim],
                            min_score=config.bm25_min_score, max_k=config.max_k)
    kept = set(keep)
    chars_after, exact = _chars_for(block, hint, len(claim), keep, render)
    before = []
    for h, sid in zip(claim, before_ids):
        bm25 = h.get("score")
        before.append({
            "sl_id": sid,
            "bm25": round(float(bm25), 6) if _is_number(bm25) else None,
            "rank": rank_of.get(sid),
        })
    return {
        "schema": SCHEMA_VERSION,
        "ts": (now or datetime.now(timezone.utc)).isoformat(),
        "session_id": session_id,
        "tool": tool,
        "project": project,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "query_terms": query.count(" OR ") + 1 if query else 0,
        "params": {"bm25_min_score": config.bm25_min_score, "max_k": config.max_k},
        "candidates": len(hits),
        "before": before,
        "after": [before_ids[i] for i in keep],
        "dropped": [sid for i, sid in enumerate(before_ids) if i not in kept],
        "chars_before": len(injected),
        "chars_after": chars_after,
        "chars_exact": exact,
        "pipeline_ms": round(max(pipeline_ms, 0.0), 3),
    }


def _append_line(path: Path, line: str) -> None:
    """單次 O_APPEND 寫入一行（無鎖、無 fsync：shadow 不是 commit point，丟一筆可接受）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = line.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"short write to {path.name}: {written}/{len(data)} bytes")
    finally:
        os.close(fd)


def record(root: Path, *, tool: str, session_id: str, project: str, query: str,
           hits: Sequence[Mapping[str, Any]], claim: Sequence[Mapping[str, Any]],
           block: str, hint: str, injected: str, config: PushShadowConfig,
           started_at: float, render: Callable[[list[int]], str] | None = None,
           log: Callable[[str], None] | None = None) -> bool:
    """計算並 append 一筆 shadow 事件。Best-effort：永不 raise，回傳是否已寫入。

    時間上限：shadow 計算（不含最後一次寫入）超過 ``config.time_budget_ms`` 即丟棄該筆，
    只記 warning——shadow 額外延遲因此被限制在「預算＋單次 append」之內。
    """
    def _warn(msg: str) -> None:
        if log is None:
            return
        try:
            log(msg)
        except Exception:
            pass

    try:
        shadow_start = clock()
        event = build_event(
            tool=tool, session_id=session_id, project=project, query=query, hits=hits,
            claim=claim, block=block, hint=hint, injected=injected, config=config,
            pipeline_ms=(shadow_start - started_at) * 1000.0, render=render)
        shadow_ms = (clock() - shadow_start) * 1000.0
        if shadow_ms > config.time_budget_ms:
            _warn(f"push shadow over time budget ({shadow_ms:.1f}ms > "
                  f"{config.time_budget_ms:.1f}ms); record dropped")
            return False
        event["shadow_ms"] = round(max(shadow_ms, 0.0), 3)
        _append_line(ledger_path(root),
                     json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except Exception as exc:
        _warn(f"push shadow failed (injection unaffected): {exc}")
        return False
