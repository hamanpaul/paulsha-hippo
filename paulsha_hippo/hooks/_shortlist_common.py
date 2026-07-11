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


def _load_offered_ids(root: Path, tool: str, session_id: str) -> set[str]:
    try:
        payload = json.loads(_offered_map_path(root, tool, session_id).read_text(encoding="utf-8"))
        by_id = payload.get("by_id")
        if not isinstance(by_id, dict):
            return set()
        return {str(k) for k in by_id.keys()}
    except Exception:
        return set()


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
    """Append 一筆 offered 事件到 append-only ledger。呼叫端持 per-session flock。"""
    led_dir = root / "runtime" / "ledger"
    led_dir.mkdir(parents=True, exist_ok=True)
    ev = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
          "tool": tool, "project": project,
          "offered": [{"sl_id": sid, "path": p} for sid, p in offered]}
    with (led_dir / "offered.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _commit_offered_map(mpath: Path, offered: list[tuple[str, str]]) -> None:
    """讀取→合併→原子替換 per-session sl_id<->path map。呼叫端須持 per-session flock。

    暫存檔 per-writer 唯一（pid＋隨機後綴）避免多 writer 互搶同名 tmp，replace
    仍原子。必須在 _session_lock 內呼叫，序列化 read→merge→replace——否則並發
    更新互相覆蓋（丟 slice）→ post-tool hook 把真實讀取誤記 offered:false。
    """
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


def _read_map_snapshot(mpath: Path) -> bytes | None:
    """Commit 前的 per-session map 原始 bytes（回滾用）；None＝檔案不存在。呼叫端持 flock。

    讀不到（不存在／IO 錯誤）一律視為「無前態」→ 回滾時直接移除本次寫入的 map
    （獨佔鎖下無其他 writer 的狀態可丟）。
    """
    try:
        return mpath.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _restore_offered_map(mpath: Path, snapshot: bytes | None) -> None:
    """把 per-session map 原子回滾到 commit 前的 snapshot。呼叫端持 per-session flock。

    snapshot 為 commit 前擷取的原始 bytes，或 None（map 原本不存在→移除本次寫入）。
    沿用與 _commit_offered_map 相同的 tmp＋原子 replace，並發 reader 不會看到半寫檔。
    """
    if snapshot is None:
        with contextlib.suppress(FileNotFoundError):
            mpath.unlink()
        return
    tmp = mpath.with_name(f".{mpath.name}.{os.getpid()}-{secrets.token_hex(4)}.rollback")
    try:
        tmp.write_bytes(snapshot)
        tmp.replace(mpath)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _publish_offered(root: Path, tool: str, session_id: str, project: str,
                     mpath: Path, offered: list[tuple[str, str]]) -> None:
    """原子發布一筆 offered 事件——per-session map 與 offered ledger 全有或全無。呼叫端持 flock。

    兩產物的可逆性不對稱：per-session map 是「tmp＋原子 replace」可逆；offered ledger
    是 append-only 不可逆，且被**跨 session 共用**（per-session flock 不序列化不同 session
    對同一 offered.jsonl 的 append）→ 絕不能事後改寫 ledger 回滾。因此排序為：

      1. 先 commit 可逆的 map（失敗→ledger 完全未動，直接 raise）；
      2. 後 append 不可逆的 ledger 作為 commit point（失敗→把 map 回滾到 snapshot 再 raise）。

    任一階段失敗都不會單邊發布：
      - map commit 失敗 → 無幽靈 ledger 事件（不灌 offered/never-read 指標、不讓
        mark-applied 的參照驗證接受從未實際送達的 slice）；
      - ledger append 失敗 → map 還原到前態（不留幽靈 seen-id 去無聲壓掉未來的真實 offer）。

    失敗時例外往外拋、shortlist pipeline 外層 fail-closed 回 ''；因兩產物皆未發布，相同
    輸入的重試會乾淨地重新嘗試（無重複 ledger 事件、無 map 汙染）。若回滾亦失敗（雙重
    故障：map 已含該 slice、ledger 未寫），ledger（漏斗真值）仍保持乾淨、不重現本 finding
    的指標膨脹，僅使該 slice 本 session 不再被 offer——記 loud warning 供恢復診斷。
    """
    snapshot = _read_map_snapshot(mpath)
    _commit_offered_map(mpath, offered)
    try:
        _append_offered_ledger(root, tool, session_id, project, offered)
    except BaseException as append_exc:
        try:
            _restore_offered_map(mpath, snapshot)
        except BaseException as rollback_exc:
            log_warn(root, tool,
                     f"offered ledger append failed AND map rollback failed; per-session "
                     f"map may retain un-published slice(s) while ledger stays clean: "
                     f"append={append_exc!r} rollback={rollback_exc!r}")
        raise


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
            # 原子發布：map commit 與 ledger append 全有或全無。任一階段失敗會 raise，
            # 由下方外層 fail-closed 回 ''——不會出現「ledger 已記 offered 但 agent 收不到
            # shortlist」的單邊發布（膨脹 offered/never-read、污染 mark-applied 參照驗證）。
            _publish_offered(root, tool, session_id, project, mpath, offered)
        return block + "\n" + _applied_hint(root, tool, session_id)
    except Exception as exc:
        log_warn(root, tool, f"shortlist failed: {exc}")
        return ""
