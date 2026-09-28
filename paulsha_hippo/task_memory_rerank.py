"""#176：task-memory R75 重排序 shadow（決策紀錄 v5 Q1；feature flag，預設關閉）。

``HIPPO_TASK_MEMORY_RERANK``：

- ``off``（預設）：什麼都不做，零 TypeSafe 呼叫、不增加延遲。
- ``shadow``：只對 public paulsha-cortex 生效。正式輸出維持 A（``moc.search(limit=3)``），本模組另外做一次
  ``moc.search(limit=12)``，經送出前過濾後以 R75-slot-v1（``h2_bench.rerank_r75``）重排，並寫一筆 shadow
  receipt。任何失敗都只記在 receipt，不影響正式輸出。

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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .importer.project_resolver import normalize_remote

RERANK_ENV = "HIPPO_TASK_MEMORY_RERANK"
DENY_TERMS_ENV = "HIPPO_TASK_MEMORY_RERANK_DENY_TERMS"
SHADOW_REPOS = frozenset({"github.com/hamanpaul/paulsha-cortex"})
# registry 解析出的 project slug 也必須是 public cortex（避免 remote 被對應到私人專案時外送）
SHADOW_PROJECT_SLUGS = frozenset({"github.com/hamanpaul/paulsha-cortex"})
MAX_JOB_INTENT_CHARS = 16 * 1024
SHADOW_LIMIT = 12
RECEIPT_SCHEMA = "hippo/task-memory-rerank-shadow/v1"


def mode(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return "shadow" if env.get(RERANK_ENV, "").strip().lower() == "shadow" else "off"


def receipt_path(memory_root: Path) -> Path:
    return Path(memory_root) / "runtime" / "experiments" / "task-memory-rerank-shadow.jsonl"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
) -> dict | None:
    """計算並回傳 shadow receipt；不屬於 shadow 範圍時回 None。本函式不丟例外。"""
    from . import h2_bench, h2_offline

    from .task_memory_provider import _DeadlineExpired

    env = os.environ if environ is None else environ
    if normalize_remote(str(request.get("project", ""))) not in SHADOW_REPOS or project_slug not in SHADOW_PROJECT_SLUGS:
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


def deferred_job(request: Mapping[str, Any], project_slug: str, payload: Mapping[str, Any]) -> dict:
    """背景 shadow 需要的最小 job：不含記憶內容，只留 note ID 與 manifest hash，確保寫入 pipe 不會阻塞。"""
    manifest = ((payload.get("delivery") or {}).get("manifest") or {})
    return {
        "request": {"task_id": request.get("task_id"), "project": request.get("project"),
                    "intent": str(request.get("intent", ""))[:MAX_JOB_INTENT_CHARS]},
        "project_slug": project_slug,
        "payload": {"candidates": [{"note_id": c.get("note_id")} for c in payload.get("candidates") or []],
                    "delivery": {"manifest": {"sha256": manifest.get("sha256")}}},
    }


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
                     environ=environ)
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
