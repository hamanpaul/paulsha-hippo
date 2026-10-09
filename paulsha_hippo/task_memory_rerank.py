"""#176：task-memory R75 重排序 shadow（決策紀錄 v5 Q1；feature flag，預設關閉）。

``HIPPO_TASK_MEMORY_RERANK``：

- ``off``（預設）：什麼都不做，零 TypeSafe 呼叫、不增加延遲。
- ``shadow``：只對 public paulsha-cortex 生效。正式輸出維持 A（``moc.search(limit=3)``），本模組另外做一次
  ``moc.search(limit=12)``，經送出前過濾後以 R75-slot-v1（``h2_bench.rerank_r75``）重排，並寫一筆 shadow
  receipt。任何失敗都只記在 receipt，不影響正式輸出。
- ``ab``：依 ``HIPPO_TASK_MEMORY_AB_SEED`` 與 task ID 穩定分組；A 組維持正式輸出並背景量測，R75 組同步
  嘗試 R75，遇到前置條件或 rerank 失敗時正式輸出回退至 A。

receipt 不保存記憶內容與原始 intent，只存 note ID、content digest、分數與排序。缺 TypeSafe key 或私有
字詞清單時直接回退，完全不送出。真正打開 shadow 需要 Paul 核准 live public-memory egress。

CLI（``hippo task-memory provide``）不同步跑 shadow：正式輸出寫出後，``spawn_detached`` 把最小 job 寫進私有
暫存檔（0600），再起一個脫離的背景行程（``python -m paulsha_hippo.task_memory_rerank --job <檔>``）計算並寫
receipt；背景行程讀完即刪除 job 檔。Hippo 主行程照常結束，不經 pipe 傳資料，不會因 shadow 逼近 Cortex 的
subprocess timeout。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .importer.project_resolver import normalize_remote

RERANK_ENV = "HIPPO_TASK_MEMORY_RERANK"
DENY_TERMS_ENV = "HIPPO_TASK_MEMORY_RERANK_DENY_TERMS"
AB_SEED_ENV = "HIPPO_TASK_MEMORY_AB_SEED"
DEFAULT_AB_SEED = "q2-2026-10"
SHADOW_REPOS = frozenset({"github.com/hamanpaul/paulsha-cortex"})
# registry 解析出的 project slug 也必須是 public cortex（避免 remote 被對應到私人專案時外送）
SHADOW_PROJECT_SLUGS = frozenset({"github.com/hamanpaul/paulsha-cortex"})
MAX_JOB_INTENT_CHARS = 16 * 1024
SHADOW_LIMIT = 12
RECEIPT_SCHEMA = "hippo/task-memory-rerank-shadow/v1"


def mode(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = env.get(RERANK_ENV, "").strip().lower()
    return value if value in {"shadow", "ab"} else "off"


def ab_arm(task_id: str, environ: Mapping[str, str] | None = None) -> str:
    """同一 task ID 在固定 seed 下總是分到相同實驗組。"""
    env = os.environ if environ is None else environ
    seed = env.get(AB_SEED_ENV, DEFAULT_AB_SEED)
    first_byte = hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).digest()[0]
    return "R75" if first_byte % 2 == 0 else "A"


def in_scope(request: Mapping[str, Any], project_slug: str) -> bool:
    return (normalize_remote(str(request.get("project", ""))) in SHADOW_REPOS
            and project_slug in SHADOW_PROJECT_SLUGS)


def receipt_path(memory_root: Path) -> Path:
    return Path(memory_root) / "runtime" / "experiments" / "task-memory-rerank-shadow.jsonl"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_receipt(*, memory_root: Path, request: Mapping[str, Any], project_slug: str,
                  production_payload: Mapping[str, Any], now: Callable[[], str] | None = None) -> dict[str, Any]:
    from . import h2_bench

    now = now or (lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    production = production_payload.get("candidates") or []
    manifest_sha = ((production_payload.get("delivery") or {}).get("manifest") or {}).get("sha256")
    return {
        "schema": RECEIPT_SCHEMA,
        "recorded_at": now(),
        "task_id": request.get("task_id"),
        "project": request.get("project"),
        "moc_search_revision": _index_revision(memory_root),
        "query_sha256": _sha256_text(str(request.get("intent", ""))),
        "production_manifest_sha256": manifest_sha,
        "production_a_top3": [c.get("note_id") for c in production],
        "shadow_r75_top3": None,
        "candidates": [],
        "fallback": None,
        "wall_ms": 0,
        "cost_usd": "0",
        "jev_model": h2_bench.JEV_MODEL,
        "deny_terms": None,
        "delivery_manifest_sha256": manifest_sha,
        "arm": None,
        "applied_arm": "A",
    }


def production_rerank(
    *, memory_root: Path,
    request: Mapping[str, Any],
    project_slug: str,
    search_fn: Callable[..., list],
    read_note: Callable[[Path, Mapping[str, Any]], dict],
    environ: Mapping[str, str] | None = None,
    jev_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """同步嘗試 R75；回傳 top hits 或一個安全回退原因，不建正式 payload。"""
    from . import h2_bench, h2_offline
    from .task_memory_provider import _DeadlineExpired

    env = os.environ if environ is None else environ
    started = time.monotonic()
    result: dict[str, Any] = {
        "selected_hits": None,
        "notes_by_id": {},
        "shadow_r75_top3": None,
        "candidates": [],
        "fallback": None,
        "wall_ms": 0,
        "cost_usd": "0",
        "deny_terms": None,
    }

    def fallback(reason: str) -> dict[str, Any]:
        result["fallback"] = reason
        result["wall_ms"] = round((time.monotonic() - started) * 1000)
        return result

    if not in_scope(request, project_slug):
        return fallback("out-of-scope")
    deny_path = env.get(DENY_TERMS_ENV, "")
    if not deny_path:
        return fallback("no-deny-terms")
    try:
        deny = h2_offline.load_deny_terms(Path(deny_path))
    except _DeadlineExpired:
        raise
    except Exception:  # noqa: BLE001 - malformed or unreadable policy fails closed to A
        return fallback("no-deny-terms")
    if not deny:
        return fallback("no-deny-terms")
    result["deny_terms"] = {"count": len(deny), "sha256": _sha256_text("\n".join(deny))}
    if not env.get("TYPESAFE_API_KEY"):
        return fallback("no-api-key")

    intent = str(request.get("intent", ""))
    try:
        hits = list(search_fn(memory_root, intent, project=project_slug,
                              limit=SHADOW_LIMIT, include_decayed=False))[:SHADOW_LIMIT]
    except _DeadlineExpired:
        raise
    except Exception:  # noqa: BLE001 - R75 failure falls back to the already-built A notes
        return fallback("search-error")

    candidates: list[dict[str, Any]] = []
    note_ids: dict[str, str] = {}
    notes_by_id: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(hits, start=1):
        if not isinstance(hit, Mapping) or hit.get("project") != project_slug:
            return fallback("scope-mismatch")
        note_id = hit.get("slice_id")
        if not isinstance(note_id, str) or not note_id:
            return fallback("scope-mismatch")
        try:
            note = read_note(memory_root, hit)
            view = note["content"].strip()[: h2_offline.BODY_VIEW_CHARS]
            title = str(note.get("summary") or hit.get("title") or "")
            reasons = h2_offline.deny_scan(f"{title}\n{view}", deny)
        except _DeadlineExpired:
            raise
        except Exception:  # noqa: BLE001 - unreadable R75 candidates cannot be delivered safely
            return fallback("read-error")
        candidates.append({"rank": rank, "title": title, "body_view": view,
                           "egress": "eligible" if not reasons else "excluded"})
        note_ids[f"c{rank}"] = note_id
        notes_by_id[note_id] = dict(note)
        result["candidates"].append({"rank": rank, "note_id": note_id,
                                     "content_sha256": _sha256_text(note["content"]),
                                     "egress": "eligible" if not reasons else "excluded",
                                     "egress_reasons": reasons, "noul": None})

    task_egress = "eligible" if not h2_offline.deny_scan(intent, deny) else "excluded"
    task = {"task_id": str(request.get("task_id")), "title": "", "body": intent,
            "task_egress": task_egress, "candidates": candidates}
    try:
        jev = (jev_factory or h2_bench.JevClient)(max_attempts=1, timeout=h2_bench.NOUL_TIMEOUT_S, environ=env)
        top, details, _jev_wall, cost = h2_bench.rerank_r75(task, jev, deny)
    except _DeadlineExpired:
        raise
    except Exception as exc:  # noqa: BLE001 - a JEV failure must fall back to A
        result["wall_ms"] = round((time.monotonic() - started) * 1000)
        result["fallback"] = "jev-timeout" if isinstance(exc, TimeoutError) else f"jev-error:{type(exc).__name__}"
        return result

    result["cost_usd"] = str(cost)
    for entry in result["candidates"]:
        entry["noul"] = details.get("noul", {}).get(f"c{entry['rank']}")
    if details.get("fallback"):
        reason = details["fallback"]
        if reason == "task-not-eligible":
            return fallback("deny")
        if reason == "too-few-eligible" and not top:
            return fallback("top-empty")
        return fallback(reason)
    selected_ids = [note_ids.get(candidate_id) for candidate_id in top]
    selected_hits = [hit for hit in hits
                     if isinstance(hit, Mapping) and hit.get("slice_id") in selected_ids]
    ordered_hits = []
    for note_id in selected_ids:
        match = next((hit for hit in selected_hits if hit.get("slice_id") == note_id), None)
        if match is None:
            return fallback("scope-mismatch")
        ordered_hits.append(match)
    if not ordered_hits:
        return fallback("top-empty")
    result.update(selected_hits=ordered_hits, notes_by_id=notes_by_id,
                  shadow_r75_top3=selected_ids,
                  wall_ms=round((time.monotonic() - started) * 1000), fallback=None)
    return result


def ab_receipt(*, memory_root: Path, request: Mapping[str, Any], project_slug: str,
               production_payload: Mapping[str, Any], arm: str, applied_arm: str,
               rerank_result: Mapping[str, Any] | None = None,
               fallback: str | None = None,
               production_a_top3: list[str | None] | None = None,
               now: Callable[[], str] | None = None) -> dict[str, Any]:
    """組裝 A/B receipt；只保存 note ID、摘要雜湊與量測欄位。"""
    receipt = _base_receipt(memory_root=memory_root, request=request, project_slug=project_slug,
                            production_payload=production_payload, now=now)
    receipt.update(arm=arm, applied_arm=applied_arm, fallback=fallback)
    if production_a_top3 is not None:
        receipt["production_a_top3"] = production_a_top3
    if rerank_result is not None:
        receipt.update(shadow_r75_top3=rerank_result.get("shadow_r75_top3"),
                       candidates=rerank_result.get("candidates") or [],
                       fallback=fallback if fallback is not None else rerank_result.get("fallback"),
                       wall_ms=rerank_result.get("wall_ms", 0),
                       cost_usd=str(rerank_result.get("cost_usd", "0")),
                       deny_terms=rerank_result.get("deny_terms"))
    receipt["delivery_manifest_sha256"] = (
        ((production_payload.get("delivery") or {}).get("manifest") or {}).get("sha256")
    )
    return receipt


def _index_revision(memory_root: Path) -> dict:
    from .moc.search import index_path

    path = index_path(Path(memory_root))
    try:
        stat = path.stat()
    except OSError:
        return {"hippo": __version__, "index": None}
    return {"hippo": __version__, "index": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}


def shadow(
    *,
    memory_root: Path,
    request: Mapping[str, Any],
    project_slug: str,
    production_payload: Mapping[str, Any],
    search_fn: Callable[..., list],
    read_note: Callable[[Path, Mapping[str, Any]], dict],
    environ: Mapping[str, str] | None = None,
    jev_factory: Callable[..., Any] | None = None,
    now: Callable[[], str] | None = None,
    arm: str | None = None,
) -> dict | None:
    """計算並回傳 shadow receipt；不屬於 shadow 範圍時回 None。本函式不丟例外。"""
    from . import h2_bench, h2_offline

    from .task_memory_provider import _DeadlineExpired

    env = os.environ if environ is None else environ
    if not in_scope(request, project_slug):
        return None
    now = now or (lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    production = production_payload.get("candidates") or []
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "recorded_at": now(),
        "task_id": request.get("task_id"),
        "project": request.get("project"),
        "moc_search_revision": _index_revision(memory_root),
        "query_sha256": _sha256_text(str(request.get("intent", ""))),
        "production_manifest_sha256": ((production_payload.get("delivery") or {}).get("manifest") or {}).get("sha256"),
        "production_a_top3": [c.get("note_id") for c in production],
        "shadow_r75_top3": None,
        "candidates": [],
        "fallback": None,
        "wall_ms": 0,
        "cost_usd": "0",
        "jev_model": h2_bench.JEV_MODEL,
        "deny_terms": None,
    }
    if arm is not None:
        receipt.update(arm=arm, applied_arm="A",
                       delivery_manifest_sha256=receipt["production_manifest_sha256"])
    try:
        deny_path = env.get(DENY_TERMS_ENV, "")
        if not deny_path:
            receipt["fallback"] = "no-deny-terms"
            return receipt
        try:
            deny = h2_offline.load_deny_terms(Path(deny_path))
        except OSError:
            receipt["fallback"] = "no-deny-terms"
            return receipt
        if not deny:
            receipt["fallback"] = "no-deny-terms"
            return receipt
        receipt["deny_terms"] = {"count": len(deny), "sha256": _sha256_text("\n".join(deny))}
        if not env.get("TYPESAFE_API_KEY"):
            receipt["fallback"] = "no-api-key"
            return receipt
        intent = str(request.get("intent", ""))
        try:
            hits = search_fn(memory_root, intent, project=project_slug, limit=SHADOW_LIMIT, include_decayed=False)
        except _DeadlineExpired:
            raise
        except Exception:  # noqa: BLE001 - shadow 失敗不得影響正式輸出
            receipt["fallback"] = "search-error"
            return receipt
        candidates, note_ids = [], {}
        for rank, hit in enumerate(list(hits)[:SHADOW_LIMIT], start=1):
            note_id = hit.get("slice_id") if isinstance(hit, Mapping) else None
            entry = {"rank": rank, "note_id": note_id, "content_sha256": None, "egress": "excluded",
                     "egress_reasons": [], "noul": None}
            try:
                if not isinstance(hit, Mapping) or hit.get("project") != project_slug:
                    raise ValueError("scope")
                note = read_note(memory_root, hit)
                view = note["content"].strip()[: h2_offline.BODY_VIEW_CHARS]
                title = str(note.get("summary") or hit.get("title") or "")
                reasons = h2_offline.deny_scan(f"{title}\n{view}", deny)
                entry.update(content_sha256=_sha256_text(note["content"]),
                             egress="eligible" if not reasons else "excluded", egress_reasons=reasons)
            except _DeadlineExpired:
                raise
            except Exception:  # noqa: BLE001
                entry["egress_reasons"] = ["read-error"]
                title, view = "", ""
            candidates.append({"rank": rank, "title": title, "body_view": view, "egress": entry["egress"]})
            note_ids[f"c{rank}"] = note_id
            receipt["candidates"].append(entry)
        task = {"task_id": str(request.get("task_id")), "title": "", "body": intent,
                "task_egress": "eligible" if not h2_offline.deny_scan(intent, deny) else "excluded",
                "candidates": candidates}
        jev = (jev_factory or h2_bench.JevClient)(max_attempts=1, timeout=h2_bench.NOUL_TIMEOUT_S, environ=env)
        top, details, wall, cost = h2_bench.rerank_r75(task, jev, deny)
        receipt.update(shadow_r75_top3=[note_ids.get(c) for c in top], fallback=details["fallback"],
                       wall_ms=wall, cost_usd=str(cost))
        for entry in receipt["candidates"]:
            entry["noul"] = details["noul"].get(f"c{entry['rank']}")
    except _DeadlineExpired:
        # provider deadline（SIGALRM）到期：交回 deadline 轉成 timeout，不可吞掉而回報成功
        raise
    except Exception as exc:  # noqa: BLE001
        receipt["fallback"] = f"shadow-error:{type(exc).__name__}"
    return receipt


def deferred_job(request: Mapping[str, Any], project_slug: str, payload: Mapping[str, Any], *, arm: str | None = None) -> dict:
    """背景 shadow 需要的最小 job：不含記憶內容，只留 note ID 與 manifest hash，確保寫入 pipe 不會阻塞。"""
    manifest = ((payload.get("delivery") or {}).get("manifest") or {})
    job = {
        "request": {"task_id": request.get("task_id"), "project": request.get("project"),
                    "intent": str(request.get("intent", ""))[:MAX_JOB_INTENT_CHARS]},
        "project_slug": project_slug,
        "payload": {"candidates": [{"note_id": c.get("note_id")} for c in payload.get("candidates") or []],
                    "delivery": {"manifest": {"sha256": manifest.get("sha256")}}},
    }
    if arm is not None:
        job["arm"] = arm
    return job


def append_receipt(memory_root: Path, receipt: Mapping[str, Any]) -> None:
    path = receipt_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def run_deferred(memory_root: Path, job: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict | None:
    """背景行程的進入點：以正式搜尋與筆記讀取計算 shadow receipt 並寫入。"""
    from .moc.search import search as moc_search
    from .task_memory_provider import _read_note

    receipt = shadow(memory_root=Path(memory_root), request=job["request"], project_slug=job["project_slug"],
                     production_payload=job["payload"], search_fn=moc_search, read_note=_read_note,
                     environ=environ, arm=job.get("arm"))
    if receipt is not None:
        append_receipt(Path(memory_root), receipt)
    return receipt


def pending_dir(memory_root: Path) -> Path:
    return Path(memory_root) / "runtime" / "experiments" / "task-memory-rerank-pending"


def spawn_detached(memory_root: Path, job: Mapping[str, Any], *, popen: Callable[..., Any] | None = None) -> None:
    """把 job 寫進私有暫存檔（0600），再起脫離 session 的背景行程處理；父行程不經 pipe 傳資料，不會阻塞。"""
    import subprocess
    import sys
    import tempfile

    directory = pending_dir(memory_root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(prefix="job-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(job, handle, ensure_ascii=False)
        os.chmod(path, 0o600)
        (popen or subprocess.Popen)(
            [sys.executable, "-m", "paulsha_hippo.task_memory_rerank", "--memory-root", str(memory_root),
             "--job", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="#176 task-memory R75 shadow（背景行程，讀取 job 檔後即刪除）")
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    job_path = Path(args.job)
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 1
    finally:
        job_path.unlink(missing_ok=True)
    try:
        run_deferred(Path(args.memory_root), job)
    except Exception:  # noqa: BLE001 - 背景 shadow 靜默失敗
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
