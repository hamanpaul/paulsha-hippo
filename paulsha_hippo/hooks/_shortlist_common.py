"""Prompt-time shortlist: bm25 search -> shortlist injection + offered recording. Best-effort IO."""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shlex
from datetime import datetime, timezone
from pathlib import Path

from paulsha_hippo.importer.project_resolver import resolve_project
from paulsha_hippo.moc import search as search_mod
from paulsha_hippo.retrieval import format_shortlist, to_fts_query
from paulsha_hippo.hooks._wakeup_common import hippo_invocation, log_warn, sanitize_id

SHORTLIST_K = 3
SHORTLIST_FETCH_K = 12


def _norm_title_key(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


def _summary(path: str, title: str = "") -> str:
    """First informative body line for the shortlist."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    lines = text.splitlines()
    # skip YAML frontmatter if present
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
        lines = lines[end + 1:]
    tkey = _norm_title_key(title)
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        s = s.lstrip("# ").strip()
        if not s:
            continue
        if tkey and _norm_title_key(s) == tkey:
            continue
        return s
    return ""


def _redact(root: Path, tool: str, project: str, session_ref: str, text: str) -> str:
    """Boundary-check memory content before it is injected to the agent (memory-consumer).

    Uses policy.check_boundary and FAILS CLOSED: if the safety check is unavailable
    (any policy load/runtime error) we return "" so no un-redacted memory text reaches
    the model context. check_boundary loads a default policy and succeeds in normal
    operation, so this only suppresses the shortlist on a genuine redaction failure.
    """
    try:
        from paulsha_hippo import policy
        return policy.check_boundary(
            "external_to_raw", text, project_slug=project or "_unknown",
            session_ref=session_ref,
        ).text
    except Exception as exc:
        log_warn(root, tool, f"shortlist redaction failed; suppressing shortlist: {exc}")
        return ""


def _offered_map_path(root: Path, tool: str, session_id: str) -> Path:
    return root / "runtime" / "wakeup" / f"{tool}__{sanitize_id(session_id)}.offered.json"


def _load_offered_ids(root: Path, tool: str, session_id: str) -> set[str]:
    try:
        payload = json.loads(_offered_map_path(root, tool, session_id).read_text(encoding="utf-8"))
        by_id = payload.get("by_id")
        if not isinstance(by_id, dict):
            return set()
        return {str(k) for k in by_id.keys()}
    except Exception:
        return set()


def _record_offered(root: Path, tool: str, session_id: str, project: str,
                    offered: list[tuple[str, str]]) -> None:
    """Append offered ledger + accumulate per-session sl_id<->path map. Best-effort.

    map 更新為「讀取→合併→原子替換」的 read-modify-write：Copilot / Claude
    prompt hook 與顯式 recall 可能對同一 (tool, session_id) 重疊執行，全程
    持 per-session flock（鎖檔固定命名、與 map 同目錄）序列化所有 writer，
    否則並發更新互相覆蓋（丟 slice）→ post-tool hook 把真實讀取誤記
    offered:false，污染漏斗歸因。暫存檔 per-writer 唯一（pid＋隨機後綴），
    replace 仍原子；持鎖進程死亡時 kernel 自動釋放 flock，不會殘留死鎖。
    """
    try:
        led_dir = root / "runtime" / "ledger"
        led_dir.mkdir(parents=True, exist_ok=True)
        ev = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
              "tool": tool, "project": project,
              "offered": [{"sl_id": sid, "path": p} for sid, p in offered]}
        with (led_dir / "offered.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

        wk_dir = root / "runtime" / "wakeup"
        wk_dir.mkdir(parents=True, exist_ok=True)
        mpath = _offered_map_path(root, tool, session_id)
        lock_path = mpath.with_name(f".{mpath.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
                cur = {"by_path": {}, "by_id": {}}
                if mpath.exists():
                    try:
                        cur = json.loads(mpath.read_text(encoding="utf-8"))
                    except Exception:
                        cur = {"by_path": {}, "by_id": {}}
                for sid, p in offered:
                    cur["by_path"][p] = sid
                    cur["by_id"][sid] = p
                tmp = mpath.with_name(
                    f".{mpath.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
                try:
                    tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(mpath)
                except BaseException:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
    except Exception as exc:
        log_warn(root, tool, f"failed to record offered: {exc}")


def _applied_hint(root: Path, tool: str, session_id: str) -> str:
    """applied 顯式訊號回報指引（契約 8）：附完整可貼命令（session 歸因已填）。"""
    argv = hippo_invocation(root) + [
        "usage", "mark-applied", "--memory-root", str(root),
        "--session-id", session_id, "--tool", tool, "--slice-id",
    ]
    cmd = " ".join(shlex.quote(arg) for arg in argv)
    return (
        f"> 若上列某條記憶實際影響了你的做法，回報 applied（--slice-id 值＝"
        f"該筆記 frontmatter 的 slice_id）：`{cmd} <slice_id>`"
    )


def build_shortlist_and_record(root: Path, tool: str, session_id: str,
                               cwd: str | None, prompt: str) -> str:
    """Resolve project, search by prompt, build shortlist, record offered. Returns '' if nothing."""
    try:
        if not prompt or prompt.lstrip().startswith("/"):
            return ""
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""
        query = to_fts_query(prompt)
        if not query:
            return ""
        try:
            hits = search_mod.search(root, query, project=project,
                                     limit=SHORTLIST_FETCH_K, include_decayed=False)
        except search_mod.SearchIndexError:
            return ""
        if not hits:
            return ""
        seen = _load_offered_ids(root, tool, session_id)
        hits = [h for h in hits if h.get("slice_id") and h["slice_id"] not in seen]
        hits = hits[:SHORTLIST_K]
        if not hits:
            return ""
        for h in hits:
            h["summary"] = _summary(h.get("path", ""), str(h.get("title") or ""))
        block = _redact(root, tool, project, session_id, format_shortlist(hits))
        if not block:
            # fail-closed: redaction suppressed the shortlist -> inject nothing and do
            # NOT record offered (nothing was surfaced to the agent).
            return ""
        offered = [(h["slice_id"], h["path"]) for h in hits if h.get("path")]
        _record_offered(root, tool, session_id, project, offered)
        return block + "\n" + _applied_hint(root, tool, session_id)
    except Exception as exc:
        log_warn(root, tool, f"shortlist failed: {exc}")
        return ""
