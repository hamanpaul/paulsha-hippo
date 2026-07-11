"""Idempotent Stage 2 memory ingestion pipeline."""

from __future__ import annotations

import fcntl
import json
import logging
import re
import shutil
import threading
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .adapters import claude, codex, copilot
from .adapters.base import AdapterResult, NormalizedSession
from . import _git
from . import registry
from . import title
from .classifier import classify_session
from .frontmatter import render_markdown
from .project_resolver import normalize_remote
from .project_resolver import resolve_project

_SCOPE_RANK = {"turn": 0, "subagent": 0, "pre_compact": 0, "session_end": 1, "watcher_final": 2}
_TERMINAL_STATUSES = {"written", "updated", "hash-duplicate", "stale-skip", "empty-skip", "self-skip"}
_LEDGER_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()

LOGGER = logging.getLogger("paulsha_hippo.importer")


class PipelineError(Exception):
    """Raised for ingestion errors that callers should present cleanly."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def content_hash(session: NormalizedSession, capture_scope: str) -> str:
    subset = (
        session["session_id"],
        capture_scope,
        session["turn_count"],
        session["ended_at"],
        sorted(session["touched_files"]),
        len(session["user_prompts"]),
    )
    return sha256(_canonical_json(subset).encode("utf-8")).hexdigest()


def completeness(session: NormalizedSession, capture_scope: str) -> tuple[int, int, int, int]:
    return (
        _SCOPE_RANK.get(capture_scope, 0),
        session["turn_count"],
        len(session["touched_files"]),
        len(session["user_prompts"]),
    )


# #7/#8 治理閘：自捕捉與空 session 短路（capture 端漏網時的第二層防護）。
_ATOMIZE_SKILL_SIGNATURES = (
    "name: atomize-knowledge-slice",
    "# Atomize Knowledge Slice",
    "把單一 session 的 fragments 蒸餾成可驗證的 knowledge slices",
)


def is_self_capture(session: NormalizedSession) -> bool:
    """#7 layer 2：user prompt 內容即 atomize skill 調用文本 → hippo 自蒸餾 session。"""
    for prompt in session.get("user_prompts", []):
        text = prompt if isinstance(prompt, str) else str(prompt)
        if any(sig in text for sig in _ATOMIZE_SKILL_SIGNATURES):
            return True
    return False


# title.apply 對空 session 的 summary 佔位符（即「無內容」訊號本身）。
_EMPTY_SUMMARY_PLACEHOLDERS = {"", "(無內容)"}


def is_empty_session(session: NormalizedSession) -> bool:
    """#8：無 user prompt、無 touched files、summary 空/佔位、turn 微量 → 無蒸餾價值。"""
    if session.get("user_prompts"):
        return False
    if session.get("touched_files"):
        return False
    if str(session.get("assistant_summary", "")).strip() not in _EMPTY_SUMMARY_PLACEHOLDERS:
        return False
    return int(session.get("turn_count", 0)) <= 1



def _read_tool(queue_path: Path) -> str:
    try:
        with queue_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise PipelineError(f"queue item not found: {queue_path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"queue item is not valid JSON: {queue_path}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"queue item must contain a top-level JSON object: {queue_path}")
    tool = payload.get("tool") or payload.get("source_agent") or payload.get("agent")
    if isinstance(tool, str) and tool:
        return tool
    raise PipelineError(f"queue item is missing tool: {queue_path}")


def _extract(queue_path: Path) -> AdapterResult:
    tool = _read_tool(queue_path)
    normalized = tool.lower().replace("_", "-")
    if normalized in {"claude", "claude-code"}:
        return claude.extract(queue_path)
    if normalized == "codex":
        return codex.extract(queue_path)
    if normalized in {"copilot", "copilot-cli", "github-copilot-cli"}:
        return copilot.extract(queue_path)
    raise PipelineError(f"unsupported tool: {tool}")


def idempotency_key(session: NormalizedSession) -> str:
    return f"{session['tool']}:{session['session_id']}"


def safe_key(key: str) -> str:
    return re.sub(r"[\\/]+", "__", key.replace(":", "__"))


_LOCK_SHARD_COUNT = 64
_SHARD_LOCK_NAME_RE = re.compile(r"^lock_shard_[0-3][0-9a-f]\.lock$")


def shard_lock_path(memory_root: Path, key: str) -> Path:
    """契約 4：importer per-key lock → 固定 64 個 hash shard。

    檔名 ``lock_shard_{h:02x}.lock``，``h = crc32(safe_key(key)) % 64``。
    取代 per-key 無界 lock 檔（#19）：碰撞只降低並行度，不影響互斥正確性。
    """
    h = zlib.crc32(safe_key(key).encode("utf-8")) % _LOCK_SHARD_COUNT
    return Path(memory_root) / "runtime" / "locks" / f"lock_shard_{h:02x}.lock"


def is_shard_lock_name(name: str) -> bool:
    """檔名是否為現行 shard lock（lock_shard_00.lock ～ lock_shard_3f.lock）。"""
    return bool(_SHARD_LOCK_NAME_RE.fullmatch(name))


def _date_parts(session: NormalizedSession) -> tuple[str, str, str]:
    captured_at = session.get("ended_at") or session.get("started_at")
    if isinstance(captured_at, str) and re.match(r"^\d{4}-\d{2}-\d{2}", captured_at):
        return captured_at, captured_at[:10], captured_at[:7]
    now = datetime.now(timezone.utc).isoformat()
    return now, now[:10], now[:7]


def _ledger_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "ledger" / "import.jsonl"


def _ledger_lock_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "locks" / "import-ledger.lock"


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _LEDGER_THREAD_LOCKS_GUARD:
        lock = _LEDGER_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LEDGER_THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def _locked_ledger(memory_root: Path):
    lock_path = _ledger_lock_path(memory_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _load_recorded(memory_root: Path, key: str) -> dict[str, Any] | None:
    ledger = _ledger_path(memory_root)
    if not ledger.exists():
        return None
    recorded: dict[str, Any] | None = None
    with ledger.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("idempotency_key") != key:
                continue
            if entry.get("status") in {"written", "updated"}:
                recorded = entry
    return recorded


def _append_ledger(memory_root: Path, entry: dict[str, Any]) -> None:
    ledger = _ledger_path(memory_root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _archive_path(memory_root: Path, month: str, key: str, status: str, incoming_hash: str) -> Path:
    archive_dir = memory_root / "archive" / "queue" / month
    stem = f"{safe_key(key)}--{safe_key(status)}--{incoming_hash[:12]}"
    candidate = archive_dir / f"{stem}.json"
    suffix = 2
    while candidate.exists():
        candidate = archive_dir / f"{stem}--{suffix}.json"
        suffix += 1
    return candidate


def _archive_queue(queue_path: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if queue_path.resolve() == archive_path.resolve():
        return
    shutil.copy2(str(queue_path), str(archive_path))


def _remove_queue(queue_path: Path) -> None:
    queue_path.unlink()


def _remove_stale_inbox(previous_inbox_path: str | None, current_inbox_path: Path) -> None:
    if not previous_inbox_path:
        return
    previous = Path(previous_inbox_path)
    if previous == current_inbox_path:
        return
    if previous.exists():
        previous.unlink()


def _decision_entry(
    *,
    status: str,
    key: str,
    queue_path: Path,
    inbox_path: Path,
    archive_path: Path,
    incoming_hash: str,
    incoming_completeness: tuple[int, int, int, int],
    recorded: dict[str, Any] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "idempotency_key": key,
        "queue_path": str(queue_path),
        "inbox_path": str(inbox_path),
        "archive_path": str(archive_path),
        "content_hash": incoming_hash,
        "completeness": list(incoming_completeness),
    }
    if recorded is not None:
        entry["from_completeness"] = recorded.get("completeness")
        entry["to_completeness"] = list(incoming_completeness)
        entry["recorded_hash"] = recorded.get("content_hash")
        entry["incoming_hash"] = incoming_hash
        entry["previous_inbox_path"] = recorded.get("inbox_path")
    return entry


def _persisted_session(session: NormalizedSession, *, raw_payload_pointer: str) -> NormalizedSession:
    persisted: NormalizedSession = dict(session)
    persisted["raw_payload_pointer"] = raw_payload_pointer
    return persisted


def _record_registry_discovery(memory_root: Path, discovery: dict[str, Any] | None) -> None:
    """Opt-in（project_registry.auto_write）時把已解析的 project mapping 寫回 registry（#14）。

    Fail-open：registry 寫入失敗不得影響 ingest 主流程，僅記 warning。
    slug 為 _unknown 或 roots+remotes 全空（非 repo、無 remote 的雜訊 session）不寫。
    """
    if not discovery:
        return
    slug = discovery.get("slug")
    roots = [item for item in discovery.get("roots", []) if item]
    remotes = [item for item in discovery.get("remotes", []) if item]
    if not slug or slug == "_unknown":
        return
    if not roots and not remotes:
        return
    if not registry.auto_write_enabled():
        return
    try:
        registry.record_discovery(
            slug=slug,
            roots=roots,
            remotes=remotes,
            registry_path=registry.default_registry_path(memory_root),
        )
    except (OSError, ValueError) as exc:
        LOGGER.warning("project registry auto-write failed (fail-open): %s", exc)


def _discovery_candidate(
    *,
    slug: str,
    main_root: str | None,
    remotes: tuple[str, ...],
    memory_root: Path,
) -> dict[str, Any] | None:
    """Discovery 寫入 gate（#14）：僅當 slug 由 remote 正規化派生時才產生 registry 候選。

    dir-name / basename fallback slug 一律 skip（記 debug log）——寫入 gate 一刀切掉
    整個污染家族：
    - 無 remote 錨定（remoteless repo / linked worktree / 非 repo 目錄）：slug 必為
      fallback 派生，寫入後經 union-read 反饋污染解析（如「worktree 目錄名 slug ↦
      主 repo root」的自我矛盾 mapping 翻轉主 repo 歸屬）。
    - 有 remote 但 slug 非由該 remote 派生（cwd 已刪的 ephemeral worktree、git 逾時
      → basename fallback）：垃圾 slug 掛真 remote，真 repo 下個 session 經 remote
      match 解析成垃圾 slug（自我強化污染）。
    remote 派生判準：slug 等於某 anchor remote 的正規形（無 config 匹配時
    resolve_project 的 raw remote slug），或等於 config/registry 以該 remote
    重解析（remote-only，不帶 cwd）出的 slug。判準逐 remote 套用：只有個別
    通過驗證的 remote 才寫入輸出 remotes——payload 夾帶不相干 remote_url 而
    slug 實由現場探測 remote 派生時，不相干 remote 不得搭便車落盤（否則真
    remote 恰為該值的無關 repo 會經 union-read remote match 被誤判成本 slug，
    自我強化污染的另一變體）。
    """
    anchor_remotes = tuple(sorted({value for value in remotes if value}))
    if not slug or slug == "_unknown" or not anchor_remotes:
        LOGGER.debug(
            "project registry discovery skipped（無 remote 錨定，fallback slug 不落盤）: slug=%s",
            slug,
        )
        return None
    validated_remotes = tuple(
        remote
        for remote in anchor_remotes
        if slug == remote or slug == resolve_project(remote_url=remote, memory_root=str(memory_root))
    )
    if not validated_remotes:
        LOGGER.debug(
            "project registry discovery skipped（slug 非 remote 派生，不落盤）: slug=%s remotes=%s",
            slug,
            anchor_remotes,
        )
        return None
    return {
        "slug": slug,
        "roots": [main_root] if main_root else [],
        "remotes": list(validated_remotes),
    }


def _preview_queue_item_unlocked(queue_item: str | Path, *, memory_root: str | Path) -> dict[str, Any]:
    queue_path = Path(queue_item)
    root = Path(memory_root)
    result = _extract(queue_path)
    session = title.apply(dict(result.session), memory_root=root)
    remote_url = result.raw_payload.get("remote_url") or result.raw_payload.get("remote") or session.get("repo")
    if not isinstance(remote_url, str):
        remote_url = None
    key = idempotency_key(session)
    incoming_hash = content_hash(session, result.capture_scope)
    incoming_completeness = completeness(session, result.capture_scope)
    captured_at, day, month = _date_parts(session)

    # #7/#8 短路：不寫 inbox、不入蒸餾佇列，僅 archive+ledger+移除 queue。
    skip_status = None
    if is_self_capture(session):
        skip_status = "self-skip"
    elif is_empty_session(session):
        skip_status = "empty-skip"
    if skip_status is not None:
        archive_path = _archive_path(root, month, key, skip_status, incoming_hash)
        decision = _decision_entry(
            status=skip_status,
            key=key,
            queue_path=queue_path,
            inbox_path=root / "inbox" / "_skipped" / f"{safe_key(session['session_id'])}.md",
            archive_path=archive_path,
            incoming_hash=incoming_hash,
            incoming_completeness=incoming_completeness,
            recorded=_load_recorded(root, key),
        )
        decision["skip_reason"] = skip_status
        decision["rendered"] = ""
        return decision
    bucket = classify_session(session)
    project = resolve_project(
        cwd=session.get("cwd"),
        git_toplevel=session.get("repo"),
        remote_url=remote_url,
        memory_root=str(root),
    )
    inbox_path = root / "inbox" / bucket / session["tool"] / day / f"{safe_key(session['session_id'])}.md"
    recorded = _load_recorded(root, key)
    route_changed = recorded is not None and (
        recorded.get("inbox_path") != str(inbox_path) or recorded.get("project") != project
    )
    if recorded is None:
        status = "written"
    elif route_changed:
        status = "updated"
    elif incoming_hash == recorded.get("content_hash"):
        status = "hash-duplicate"
    elif tuple(incoming_completeness) > tuple(recorded.get("completeness", [])):
        status = "updated"
    else:
        status = "stale-skip"
    archive_path = _archive_path(root, month, key, status, incoming_hash)
    rendered_session = _persisted_session(session, raw_payload_pointer=str(archive_path))
    discovered_toplevel = _git.git_toplevel(session.get("cwd"))
    discovered_remote = normalize_remote(_git.git_remote(discovered_toplevel))
    provenance_repo = discovered_remote or "_unknown"
    main_root = _git.git_main_toplevel(discovered_toplevel)
    # 持久化面只信顯式 remote 鍵（remote_url / remote）：remote_url 的 fallback 鏈含
    # session['repo']（toplevel 路徑形輸入，僅供 resolve_project 比對 match-only），
    # normalize_remote 會把路徑變造成假 remote（work/...、github.com/a/b），不得寫入 registry（#14）。
    explicit_remote = result.raw_payload.get("remote_url") or result.raw_payload.get("remote")
    payload_remote = normalize_remote(explicit_remote) if isinstance(explicit_remote, str) else ""
    # discovery 的 slug 必須與 roots 同源（#14）：roots 記歸併後的主 repo root，但
    # project 是以 session cwd（可能是 linked worktree）解析。remoteless worktree 會
    # 落到 worktree 目錄名 fallback，寫入「worktree 名 slug ↦ 主 repo root」的自我
    # 矛盾 mapping，union-read 後反饋汙染主 repo 本體 session 的歸屬。linked worktree
    # 情境一律改以 main_root 重新推導 slug——依構造保證該 root 的未來解析結果就是
    # 此 slug；有 remote 時 worktree 與主 checkout 共用 remote，推導結果不變。
    discovery_slug = project
    if main_root and discovered_toplevel and Path(main_root) != Path(discovered_toplevel):
        discovery_slug = resolve_project(
            cwd=main_root,
            git_toplevel=main_root,
            remote_url=remote_url,
            memory_root=str(root),
        )
    decision = _decision_entry(
        status=status,
        key=key,
        queue_path=queue_path,
        inbox_path=inbox_path,
        archive_path=archive_path,
        incoming_hash=incoming_hash,
        incoming_completeness=incoming_completeness,
        recorded=recorded,
    )
    decision["classifier_bucket"] = bucket
    decision["project"] = project
    decision["discovery"] = _discovery_candidate(
        slug=discovery_slug,
        main_root=main_root,
        remotes=(payload_remote, discovered_remote),
        memory_root=root,
    )
    decision["rendered"] = render_markdown(
        rendered_session,
        project=project,
        classifier_bucket=bucket,
        captured_at=captured_at,
        provenance_repo=provenance_repo,
    )
    return decision


def preview_queue_item(queue_item: str | Path, *, memory_root: str | Path) -> dict[str, Any]:
    root = Path(memory_root)
    with _locked_ledger(root):
        return _preview_queue_item_unlocked(queue_item, memory_root=root)


def ingest_queue_item(queue_item: str | Path, *, memory_root: str | Path, dry_run: bool = False) -> dict[str, Any]:
    queue_path = Path(queue_item)
    root = Path(memory_root)
    decision = preview_queue_item(queue_path, memory_root=root)
    rendered = decision.pop("rendered")
    if dry_run:
        decision["dry_run"] = True
        decision["rendered"] = rendered
        return decision

    key = decision["idempotency_key"]
    lock_dir = root / "runtime" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{safe_key(key)}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            with _locked_ledger(root):
                decision = _preview_queue_item_unlocked(queue_path, memory_root=root)
                rendered = decision.pop("rendered")
                discovery = decision.pop("discovery", None)
                inbox_path = Path(decision["inbox_path"])
                archive_path = Path(decision["archive_path"])
                if decision["status"] in {"written", "updated"}:
                    _archive_queue(queue_path, archive_path)
                    _atomic_write(inbox_path, rendered)
                    _remove_stale_inbox(decision.get("previous_inbox_path"), inbox_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
                elif decision["status"] in _TERMINAL_STATUSES:
                    _archive_queue(queue_path, archive_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
            _record_registry_discovery(root, discovery)
            if discovery is not None:
                decision["discovery"] = discovery
            return decision
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
