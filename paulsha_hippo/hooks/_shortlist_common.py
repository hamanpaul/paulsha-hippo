"""Prompt-time shortlist: bm25 search -> shortlist injection + offered recording. Best-effort IO."""
from __future__ import annotations

import contextlib
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
from paulsha_hippo.hooks._wakeup_common import (
    hippo_invocation, log_warn, sanitize_id, validate_tool,
)

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
    """Per-session offered map path（唯一構點）。

    tool 可能來自外部輸入（`hippo recall --tool`）：先驗證為 path-safe token，
    再 resolve 確認落點 parent 仍是 runtime/wakeup（防 sanitizer 迴歸與 symlink
    偷渡）——否則 `--tool ../../x` 會讓後續原子 replace 把檔案寫出 memory root。
    """
    wk_dir = root / "runtime" / "wakeup"
    path = wk_dir / f"{validate_tool(tool)}__{sanitize_id(session_id)}.offered.json"
    if path.resolve().parent != wk_dir.resolve():
        raise ValueError(f"offered map path escapes runtime/wakeup: {path}")
    return path


def _offered_pairs_from_ledger(root: Path, tool: str, session_id: str) -> list[tuple[str, str]]:
    """從 append-only offered ledger 還原本 (tool, session_id) 曾 offer 的 (sl_id, path) 清單。

    ledger 是 offered 的**單一真值**、跨 session 共用（同一 offered.jsonl）；依 session_id＋tool
    過濾出本 session 的事件。per-session map 為此清單的衍生 cache——硬中止或 cache 寫入失敗
    落在「ledger 有、map 無」時，這裡是重建 map／判定 offered 的權威來源。讀不到（不存在／IO／
    單行壞）一律略過該來源（fail-open），不讓恢復路徑因殘缺 ledger 崩掉。
    """
    pairs: list[tuple[str, str]] = []
    try:
        raw = (root / "runtime" / "ledger" / "offered.jsonl").read_text(encoding="utf-8")
    except OSError:
        return pairs
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("session_id") != session_id or ev.get("tool") != tool:
            continue
        for item in ev.get("offered") or []:
            sid = item.get("sl_id") if isinstance(item, dict) else None
            p = item.get("path") if isinstance(item, dict) else None
            if sid and p:
                pairs.append((str(sid), str(p)))
    return pairs


def _load_offered_ids(root: Path, tool: str, session_id: str) -> set[str]:
    """本 (tool, session_id) 已 offer 的 sl_id 集合——以 offered ledger（單一真值）為準。

    回傳 per-session map 的 sl_id ∪ offered ledger 的 sl_id。map 是衍生 cache：反轉發布順序
    （先 ledger 後 map）後，硬中止／cache 寫入失敗可能落在「ledger 有、map 無」，聯集 ledger
    使去重判定不漏掉已 offer 的 slice——既不重複送達、也不永久去重。純讀取、不持鎖、不寫檔，
    可安全在既有 per-session flock 臨界區內呼叫（reconcile 已於同鎖內先補齊 map）。map 讀取
    失敗（不存在／壞檔／非法 tool）視為空集合（fail-open），仍以 ledger 補全。
    """
    ids: set[str] = set()
    try:
        payload = json.loads(_offered_map_path(root, tool, session_id).read_text(encoding="utf-8"))
        by_id = payload.get("by_id")
        if isinstance(by_id, dict):
            ids.update(str(k) for k in by_id.keys())
    except Exception:
        pass
    ids.update(sid for sid, _ in _offered_pairs_from_ledger(root, tool, session_id))
    return ids


@contextlib.contextmanager
def _session_lock(mpath: Path):
    """Per-session 排他 flock（鎖檔固定命名、與 offered map 同目錄）。

    序列化同一 (tool, session_id) 的所有 writer。完整 recall 管線在此鎖內
    從「重讀 seen」一路持有到「ledger append＋map commit」，兩個進程對同一
    session 併發跑 build_shortlist_and_record 時不會各自讀到空 seen 而重複
    claim 同一 slice（重複曝光＋offered_count 膨脹）；直接呼叫 _record_offered
    的 writer 也共用同一把鎖，彼此互斥。持鎖進程死亡時 kernel 自動釋放，不
    殘留死鎖。
    """
    mpath.parent.mkdir(parents=True, exist_ok=True)
    lock_path = mpath.with_name(f".{mpath.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _append_offered_ledger(root: Path, tool: str, session_id: str, project: str,
                           offered: list[tuple[str, str]]) -> None:
    """Append 一筆 offered 事件到 append-only ledger 並 fsync 落盤。呼叫端持 per-session flock。

    ledger 是 offered 的單一真值＋crash commit point：write 後 flush＋fsync 確保硬中止
    （SIGKILL／hook timeout／主機中斷）發生前已落盤——否則資料僅留在 Python／OS 緩衝，強制
    終止會連同「map 尚未更新」一起遺失，重現本 finding 的缺口（見 _publish_offered）。
    """
    led_dir = root / "runtime" / "ledger"
    led_dir.mkdir(parents=True, exist_ok=True)
    ev = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
          "tool": tool, "project": project,
          "offered": [{"sl_id": sid, "path": p} for sid, p in offered]}
    with (led_dir / "offered.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _commit_offered_map(mpath: Path, offered: list[tuple[str, str]]) -> None:
    """讀取→合併→原子替換 per-session sl_id<->path map。呼叫端須持 per-session flock。

    暫存檔 per-writer 唯一（pid＋隨機後綴）避免多 writer 互搶同名 tmp，replace
    仍原子。必須在 _session_lock 內呼叫，序列化 read→merge→replace——否則並發
    更新互相覆蓋（丟 slice）→ post-tool hook 把真實讀取誤記 offered:false。
    """
    mpath.parent.mkdir(parents=True, exist_ok=True)
    cur = {"by_path": {}, "by_id": {}}
    if mpath.exists():
        try:
            cur = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            cur = {"by_path": {}, "by_id": {}}
    for sid, p in offered:
        cur["by_path"][p] = sid
        cur["by_id"][sid] = p
    tmp = mpath.with_name(f".{mpath.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        tmp.replace(mpath)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _reconcile_offered_map(root: Path, tool: str, session_id: str, mpath: Path) -> None:
    """以 offered ledger（單一真值）補齊 per-session map——ledger 有但 map 無的 slice 寫回 map。呼叫端持 flock。

    map 是 ledger 的衍生 cache。反轉發布順序（先 ledger 後 map）後，硬中止或 map cache 寫入
    失敗會落在「ledger 有、map 無」；在同一把 per-session flock 內、claim 去重前先 reconcile，
    把 ledger 的 offered slice 補回 map，使 (a) 去重判定回到 ledger 真值、(b) post-tool 讀取端
    直接讀 map by_path 時拿到的即 ledger 可重建的真值。無缺漏時不寫檔（省去多餘的原子 replace，
    維持既有 map 冪等）。map 讀取失敗（壞檔）視為空 → 由 _commit_offered_map 以 ledger 重建。

    補寫（_commit_offered_map）為 **best-effort**：ledger 才是單一真值、_load_offered_ids 已無
    條件聯集 ledger sl_id，去重正確性不依賴這次快取健化是否成功。補寫失敗（磁碟滿／權限／IO——
    正是 _publish_offered 明文容忍的同一類故障）僅 log_warn、不 raise，避免無關的儲存層故障讓整輪
    claim/redact/publish fail-closed 而吞掉全新、從未 offer 過的命中 slice；下一輪仍會以 ledger 重試。
    """
    pairs = _offered_pairs_from_ledger(root, tool, session_id)
    if not pairs:
        return
    by_id: dict = {}
    try:
        payload = json.loads(mpath.read_text(encoding="utf-8"))
        if isinstance(payload.get("by_id"), dict):
            by_id = payload["by_id"]
    except Exception:
        by_id = {}
    missing = [(sid, p) for sid, p in pairs if by_id.get(sid) != p]
    if missing:
        # best-effort cache 健化（比照 _publish_offered 對同一 _commit_offered_map 的容忍）：
        # 補寫失敗只 log_warn、不 raise，讓本輪 claim/redact/publish 照常進行——ledger 為單一
        # 真值，_load_offered_ids 已聯集 ledger，去重正確性不依賴這次補寫；下輪仍以 ledger 重試。
        try:
            _commit_offered_map(mpath, missing)
        except Exception as exc:
            log_warn(root, tool,
                     f"offered map reconcile write failed; ledger remains authoritative "
                     f"and map will be retried from ledger next round: {exc}")


def _publish_offered(root: Path, tool: str, session_id: str, project: str,
                     mpath: Path, offered: list[tuple[str, str]]) -> None:
    """發布一筆 offered 事件——offered ledger 為單一真值＋commit point，per-session map 為可重建 cache。呼叫端持 flock。

    排序反轉為「先 ledger、後 map」並讓 ledger fsync 落盤，關閉前輪 map-先寫留下的
    crash-consistency 缺口：舊序在 map commit 與 ledger append 之間被 SIGKILL／hook timeout
    強制中止時，map 已標記 slice 為 seen 但 ledger 無事件 → 該 slice 永久被去重、不再送達
    （違反全有或全無）；前輪只修可捕捉例外路徑（rollback），涵蓋不到硬中止。

      1. 先 append 不可逆的 offered ledger 並 fsync（durable commit point）。失敗 → 尚無任何
         產物發布，raise 由外層 fail-closed，相同輸入可乾淨重試（無半發布、無重複事件）。
      2. 再更新可重建的 map cache。硬中止只會落在「ledger 有、map 無」＝安全側：map 為 ledger
         衍生，下一輪 _reconcile_offered_map／_load_offered_ids 以 ledger 為準補齊——既不永久遺漏、
         也不重複送達。

    map 更新失敗不回滾、不外拋（ledger 已是真值，其失敗態等同硬中止落點，由 reconcile 重建）：
    僅記 warning。若在此 fail-closed，會使 agent 收不到「已 commit」的 offer、重現前輪
    offered-but-undelivered 的指標膨脹，故降級為 best-effort cache 更新。
    """
    _append_offered_ledger(root, tool, session_id, project, offered)
    try:
        _commit_offered_map(mpath, offered)
    except Exception as exc:
        log_warn(root, tool,
                 f"offered map cache update failed after ledger commit; map will be "
                 f"rebuilt from ledger on next reconcile: {exc}")


def _record_offered(root: Path, tool: str, session_id: str, project: str,
                    offered: list[tuple[str, str]]) -> None:
    """原子發布 offered（map commit＋ledger append），全程持 per-session flock。Best-effort。

    保留給直接呼叫端（顯式 recall 只記帳、既有並發測試）。完整 shortlist 管線改由
    build_shortlist_and_record 在同一把鎖內連同「重讀 seen → claim」一起持有（見該函式），
    使去重判定與 claim 對同一 session 原子。兩者皆委由 _publish_offered 原子發布——map
    commit 與 ledger append 全有或全無，任一階段失敗不單邊發布。
    """
    try:
        mpath = _offered_map_path(root, tool, session_id)
        with _session_lock(mpath):
            _publish_offered(root, tool, session_id, project, mpath, offered)
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
        # tool 進入 offered-map 檔名；recall 的 --tool 為外部輸入——非法即整條
        # pipeline fail-closed（不注入、不記 offered），不讓歸因破損的 shortlist 流出。
        validate_tool(tool)
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
        # tool 已於函式頂端驗證；建 offered-map 路徑（_offered_map_path 內含第二層
        # traversal 防護）並持 per-session flock，把「重讀 seen → claim hits → redact →
        # 原子發布（map commit + ledger append）」整段併入同一原子臨界區——兩個進程對同一
        # (tool, session_id) 併發時不會各讀空 seen 而重複 claim／重複曝光同一 slice。
        # search 屬只讀且不需序列化，留在鎖外。
        mpath = _offered_map_path(root, tool, session_id)
        with _session_lock(mpath):
            # map 為 offered ledger 的衍生 cache：claim 去重前先以 ledger（單一真值）reconcile，
            # 補齊硬中止／前次 cache 寫入失敗殘留的「ledger 有、map 無」，使去重判定與 post-tool
            # 讀取端皆回到 ledger 可重建的真值（seen 亦已聯集 ledger，reconcile 另把真值落回 map）。
            _reconcile_offered_map(root, tool, session_id, mpath)
            seen = _load_offered_ids(root, tool, session_id)
            claim = [h for h in hits
                     if h.get("slice_id") and h["slice_id"] not in seen][:SHORTLIST_K]
            if not claim:
                return ""
            for h in claim:
                h["summary"] = _summary(h.get("path", ""), str(h.get("title") or ""))
            block = _redact(root, tool, project, session_id, format_shortlist(claim))
            if not block:
                # fail-closed: redaction suppressed the shortlist -> inject nothing and do
                # NOT record offered (nothing was surfaced to the agent).
                return ""
            offered = [(h["slice_id"], h["path"]) for h in claim if h.get("path")]
            # 發布：先 fsync offered ledger（單一真值＋commit point）後更新 map cache。ledger
            # append 失敗會 raise，由下方外層 fail-closed 回 ''——不出現「ledger 已記但 agent 收不到
            # shortlist」的膨脹；ledger 成功後的硬中止只落在「ledger 有、map 無」安全側，由 reconcile
            # 重建（見 _publish_offered）。
            _publish_offered(root, tool, session_id, project, mpath, offered)
        return block + "\n" + _applied_hint(root, tool, session_id)
    except Exception as exc:
        log_warn(root, tool, f"shortlist failed: {exc}")
        return ""
