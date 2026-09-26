"""#151：archive GC——回收「對應 session 已落成 knowledge」的 archive 檔。

權威來源是 hippo 既有的處理紀錄，不猜測：
  - `runtime/ledger/processing.jsonl`（fold 後最新狀態）決定 session 是否已落成；
    預設只有 `promoted` 算落成，`--include-no-findings` 才把 `no-findings` 納入。
  - `archive/sessions`、`archive/fragments` 依 atomizer 的固定命名
    （`{agent}__{session}[--hash12].md`、`{agent}__{session}__NNN[--hash12].md`）
    對回 session，刪除前再以檔內 frontmatter 的 source_agent／source_session 複驗。
  - `archive/queue` 依 importer 的 `runtime/ledger/import.jsonl`（archive_path →
    logical_session_key）對回 session。

一律保留（原因記入報告 `kept.by_reason`）：
  - unattributable：無法歸因（命名不符、不在 import ledger、同名歧義）
  - no-processing-record／not-landed:<state>：未落成（split／parked／pending…）
  - pending-inbox：inbox 仍有該 session 的新 capture 或 `_slices` fragment
  - provenance-pinned：被 knowledge／inbox 的 `provenance.path` 引用——刪了會讓
    knowledge 的 provenance 懸空（janitor `check_provenance_path` 會誤判 source_invalid）
  - retention-window：落成時間或檔案 mtime 仍在保留窗內
  - attribution-mismatch：檔名歸屬與 frontmatter 不一致
  - not-regular-file／unexpected-layout：symlink、目錄或非預期層級

安全性：預設 dry-run 不寫任何檔；`apply=True` 須取得 dream global lock（與 dream
run 互斥），在持鎖下重新規劃，再以 dir fd + O_NOFOLLOW 逐檔核對 inode 後 unlink。
archive 或其子樹為 symlink 時拒絕 apply。只刪檔、不刪目錄；重跑冪等。
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .ledger import processing

SUBTREES: tuple[str, ...] = ("sessions", "fragments", "queue")
DEFAULT_RETENTION_DAYS = 7.0
_FRONTMATTER_HEAD_BYTES = 64 * 1024
_HASH_SUFFIX = re.compile(r"--[0-9a-f]{12}$")
_FRAGMENT_INDEX = re.compile(r"__\d{3,}$")
_AMBIGUOUS = object()


class ArchiveGCError(Exception):
    """GC 無法安全規劃（權威紀錄不可讀等）。"""


@dataclass(frozen=True)
class _Entry:
    rel: str
    subtree: str
    month: str
    name: str
    size: int
    mtime: float
    ino: int
    dev: int
    mode: int


@dataclass(frozen=True)
class _Candidate:
    entry: _Entry
    session_key: str


# ----------------------------------------------------------------- parsing


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_frontmatter(path: Path) -> dict[str, Any] | None:
    """只讀檔頭解析 YAML frontmatter；讀不到或格式不符回 None（呼叫端一律保守）。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_FRONTMATTER_HEAD_BYTES)
    except OSError:
        return None
    text = head.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    block: list[str] = []
    for line in lines[1:]:
        if line.rstrip("\r") == "---":
            break
        block.append(line)
    else:
        return None
    try:
        import yaml

        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        data = yaml.load("\n".join(block), Loader=loader)  # noqa: S506 — SafeLoader
    except Exception:  # noqa: BLE001 — 壞 frontmatter 一律視為不可歸因
        return None
    return data if isinstance(data, dict) else None


def _archive_rel(path_value: object) -> str | None:
    """把 provenance／ledger 內記錄的 archive 絕對路徑正規化成 `archive/...` 相對路徑。

    以最後一個 `/archive/` 段切分，memory_root 搬遷（路徑前綴改變）後仍能對上。
    """
    if not isinstance(path_value, str) or not path_value:
        return None
    normalized = path_value.replace("\\", "/")
    marker = "/archive/"
    index = normalized.rfind(marker)
    if index < 0:
        return normalized if normalized.startswith("archive/") else None
    return normalized[index + 1:]


def _iter_markdown(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(dirpath) / name


# ----------------------------------------------------------- authority maps


def _prefix_map(events: dict[str, dict[str, Any]]) -> dict[str, object]:
    """`{agent}__{session}` → session_key；多個 key 撞同一前綴時標記歧義。"""
    mapping: dict[str, object] = {}
    for key in events:
        agent, sep, session = key.partition(":")
        if not sep or not agent or not session:
            continue
        prefix = f"{agent}__{session}"
        existing = mapping.get(prefix)
        if existing is None:
            mapping[prefix] = key
        elif existing != key:
            mapping[prefix] = _AMBIGUOUS
    return mapping


def _import_map(memory_root: Path) -> dict[str, object]:
    """import ledger：`archive/queue/...` 相對路徑 → logical session key。"""
    from .importer.pipeline import _logical_key_from_entry

    ledger = memory_root / "runtime" / "ledger" / "import.jsonl"
    mapping: dict[str, object] = {}
    if not ledger.is_file():
        return mapping
    with ledger.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # 壞行：對應檔案將歸 unattributable（保留）
            if not isinstance(entry, dict):
                continue
            rel = _archive_rel(entry.get("archive_path"))
            logical = _logical_key_from_entry(entry)
            if not rel or not logical:
                continue
            existing = mapping.get(rel)
            if existing is None:
                mapping[rel] = logical
            elif existing != logical:
                mapping[rel] = _AMBIGUOUS
    return mapping


def _inbox_state(memory_root: Path) -> tuple[set[str], set[str], set[str]]:
    """回傳 (pending session keys, pending `_slices` 前綴, inbox provenance pins)。"""
    inbox = memory_root / "inbox"
    slices = inbox / "_slices"
    pending_keys: set[str] = set()
    pending_prefixes: set[str] = set()
    pins: set[str] = set()
    for path in _iter_markdown(inbox):
        if slices == path.parent or slices in path.parents:
            stem = _HASH_SUFFIX.sub("", path.stem)
            pending_prefixes.add(_FRAGMENT_INDEX.sub("", stem))
            continue
        data = _read_frontmatter(path)
        if data is None:
            continue
        agent, session = data.get("source_agent"), data.get("source_session")
        if agent and session:
            pending_keys.add(f"{agent}:{session}")
        pin = _provenance_rel(data)
        if pin:
            pins.add(pin)
    return pending_keys, pending_prefixes, pins


def _provenance_rel(data: dict[str, Any]) -> str | None:
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        return None
    return _archive_rel(provenance.get("path"))


def _knowledge_pins(memory_root: Path) -> set[str]:
    pins: set[str] = set()
    for path in _iter_markdown(memory_root / "knowledge"):
        data = _read_frontmatter(path)
        if data is None:
            continue
        pin = _provenance_rel(data)
        if pin:
            pins.add(pin)
    return pins


# ------------------------------------------------------------------ walking


def _walk_archive(archive: Path, kept: dict[str, list[_Entry]],
                  unsafe_dirs: list[str]) -> list[_Entry]:
    entries: list[_Entry] = []
    if not os.path.lexists(archive):
        return entries
    if archive.is_symlink() or not archive.is_dir():
        unsafe_dirs.append("archive")
        return entries
    for subtree in SUBTREES:
        sub_dir = archive / subtree
        if not os.path.lexists(sub_dir):
            continue
        if sub_dir.is_symlink() or not sub_dir.is_dir():
            unsafe_dirs.append(f"archive/{subtree}")
            continue
        for month in _sorted_scandir(sub_dir):
            month_rel = f"archive/{subtree}/{month.name}"
            if not month.is_dir(follow_symlinks=False):
                entry = _stat_entry(month.path, month_rel, subtree, "", month.name)
                if entry is not None:
                    kept["unexpected-layout"].append(entry)
                continue
            for item in _sorted_scandir(Path(month.path)):
                entry = _stat_entry(item.path, f"{month_rel}/{item.name}", subtree, month.name, item.name)
                if entry is None:
                    continue  # 列舉瞬間消失
                if stat.S_ISDIR(entry.mode):
                    kept["unexpected-layout"].append(entry)
                elif not stat.S_ISREG(entry.mode):
                    kept["not-regular-file"].append(entry)
                else:
                    entries.append(entry)
    return entries


def _sorted_scandir(path: Path) -> list[os.DirEntry[str]]:
    with os.scandir(path) as iterator:
        return sorted(iterator, key=lambda item: item.name)


def _stat_entry(path: str, rel: str, subtree: str, month: str, name: str) -> _Entry | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return _Entry(rel, subtree, month, name, st.st_size, st.st_mtime, st.st_ino, st.st_dev, st.st_mode)


# ----------------------------------------------------------------- planning


def _attribute(entry: _Entry, prefixes: dict[str, object],
               imports: dict[str, object]) -> str | None:
    """回傳檔案所屬 session_key；無法（或無法唯一）歸因回 None。"""
    if entry.subtree == "queue":
        key = imports.get(entry.rel)
        return key if isinstance(key, str) else None
    if not entry.name.endswith(".md"):
        return None
    stem = _HASH_SUFFIX.sub("", entry.name[: -len(".md")])
    if entry.subtree == "fragments":
        stripped = _FRAGMENT_INDEX.sub("", stem)
        if stripped == stem:
            return None
        stem = stripped
    key = prefixes.get(stem)
    return key if isinstance(key, str) else None


def _frontmatter_matches(memory_root: Path, entry: _Entry, session_key: str) -> bool:
    if entry.subtree == "queue":
        return True  # import ledger 已是逐檔權威紀錄
    data = _read_frontmatter(memory_root / entry.rel)
    if data is None:
        return False
    agent, _, session = session_key.partition(":")
    return str(data.get("source_agent", "")) == agent and str(data.get("source_session", "")) == session


def _plan(memory_root: Path, *, now: datetime, retention_days: float,
          landed_states: frozenset[str]) -> dict[str, Any]:
    try:
        events = processing.fold_events(memory_root)
    except (processing.ProcessingLedgerError, OSError, UnicodeDecodeError) as exc:
        raise ArchiveGCError(f"processing ledger 無法讀取：{processing.sanitize_error_text(str(exc))}") from exc
    prefixes = _prefix_map(events)
    imports = _import_map(memory_root)
    pending_keys, pending_prefixes, pins = _inbox_state(memory_root)
    pins |= _knowledge_pins(memory_root)

    kept: dict[str, list[_Entry]] = {"unexpected-layout": [], "not-regular-file": []}
    unsafe_dirs: list[str] = []
    entries = _walk_archive(memory_root / "archive", kept, unsafe_dirs)
    window = timedelta(days=retention_days)
    candidates: list[_Candidate] = []

    def keep(reason: str, entry: _Entry) -> None:
        kept.setdefault(reason, []).append(entry)

    for entry in entries:
        key = _attribute(entry, prefixes, imports)
        if key is None:
            keep("unattributable", entry)
            continue
        event = events.get(key)
        if event is None:
            keep("no-processing-record", entry)
            continue
        state = str(event.get("state", "")) or "unknown"
        if state not in landed_states:
            keep(f"not-landed:{state}", entry)
            continue
        agent, _, session = key.partition(":")
        if key in pending_keys or f"{agent}__{session}" in pending_prefixes:
            keep("pending-inbox", entry)
            continue
        if entry.rel in pins:
            keep("provenance-pinned", entry)
            continue
        landed_at = _parse_ts(event.get("ts"))
        if landed_at is None:
            keep("unknown-landed-at", entry)
            continue
        mtime = datetime.fromtimestamp(entry.mtime, tz=timezone.utc)
        if now - max(landed_at, mtime) < window:
            keep("retention-window", entry)
            continue
        if not _frontmatter_matches(memory_root, entry, key):
            keep("attribution-mismatch", entry)
            continue
        candidates.append(_Candidate(entry, key))

    return {"candidates": candidates, "kept": kept, "unsafe_dirs": unsafe_dirs,
            "scanned": entries + kept["unexpected-layout"] + kept["not-regular-file"]}


def _tally(entries: Iterable[_Entry]) -> dict[str, int]:
    items = list(entries)
    return {"files": len(items), "bytes": sum(item.size for item in items)}


# ----------------------------------------------------------------- deleting


def _delete(memory_root: Path, candidates: list[_Candidate]) -> dict[str, Any]:
    """以 dir fd 逐層 O_NOFOLLOW 開啟 archive/<subtree>/<month>，核對 inode 後 unlink。"""
    cloexec = getattr(os, "O_CLOEXEC", 0)
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | cloexec
    deleted: list[_Entry] = []
    missing = 0
    failed: list[str] = []
    groups: dict[tuple[str, str], list[_Candidate]] = {}
    for candidate in candidates:
        groups.setdefault((candidate.entry.subtree, candidate.entry.month), []).append(candidate)
    try:
        anchor = os.open(memory_root, os.O_RDONLY | os.O_DIRECTORY | cloexec)
    except OSError:
        return {"deleted": [], "missing": 0,
                "failed": [c.entry.rel for c in candidates]}
    try:
        archive_fd = os.open("archive", dir_flags, dir_fd=anchor)
    except OSError:
        os.close(anchor)
        return {"deleted": [], "missing": 0, "failed": [c.entry.rel for c in candidates]}
    try:
        for (subtree, month), group in sorted(groups.items()):
            try:
                sub_fd = os.open(subtree, dir_flags, dir_fd=archive_fd)
            except OSError:
                failed.extend(c.entry.rel for c in group)
                continue
            try:
                try:
                    month_fd = os.open(month, dir_flags, dir_fd=sub_fd)
                except FileNotFoundError:
                    missing += len(group)
                    continue
                except OSError:
                    failed.extend(c.entry.rel for c in group)
                    continue
                try:
                    for candidate in group:
                        entry = candidate.entry
                        try:
                            st = os.stat(entry.name, dir_fd=month_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            missing += 1
                            continue
                        except OSError:
                            failed.append(entry.rel)
                            continue
                        if (not stat.S_ISREG(st.st_mode) or st.st_ino != entry.ino
                                or st.st_dev != entry.dev or st.st_size != entry.size):
                            failed.append(entry.rel)  # 規劃後被換掉：不刪
                            continue
                        try:
                            os.unlink(entry.name, dir_fd=month_fd)
                        except FileNotFoundError:
                            missing += 1
                            continue
                        except OSError:
                            failed.append(entry.rel)
                            continue
                        deleted.append(entry)
                finally:
                    os.close(month_fd)
            finally:
                os.close(sub_fd)
    finally:
        os.close(archive_fd)
        os.close(anchor)
    return {"deleted": deleted, "missing": missing, "failed": failed}


def _write_list(path: Path, candidates: list[_Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            entry = candidate.entry
            handle.write(f"{entry.rel}\t{entry.size}\t{candidate.session_key}\n")
    tmp.replace(path)


# --------------------------------------------------------------------- entry


def run_archive_gc(
    memory_root: str | Path,
    *,
    now: datetime | None = None,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    include_no_findings: bool = False,
    apply: bool = False,
    list_out: str | Path | None = None,
) -> dict[str, Any]:
    """規劃（並在 `apply=True` 時執行）archive GC；回傳 JSON-able 報告。

    報告含 `error`（權威紀錄不可讀）或 `blocked`（apply 前置條件不成立）時，
    保證一檔未刪。
    """
    if not math.isfinite(retention_days) or retention_days < 0:
        raise ValueError("retention_days must be a finite number >= 0")
    root = Path(memory_root).expanduser()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    landed = frozenset({"promoted"} | ({"no-findings"} if include_no_findings else set()))
    report: dict[str, Any] = {
        "memory_root": str(root),
        "now": current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "retention_days": retention_days,
        "landed_states": sorted(landed),
        "applied": False,
        "list_out": str(list_out) if list_out is not None else None,
    }

    lock_handle = None
    if apply:
        from .dream.lock import acquire_dream_lock

        if not (root / "runtime").is_dir():
            report.update(_empty_summary(apply=apply))
            report["blocked"] = "memory_root 缺 runtime/：無法確立 dream lock；拒絕 apply"
            return report
        try:
            lock_handle = acquire_dream_lock(root)
        except OSError as exc:
            report.update(_empty_summary(apply=apply))
            report["blocked"] = f"dream lock 無法取得（{type(exc).__name__}）；拒絕 apply"
            return report
        if lock_handle is None:
            report.update(_empty_summary(apply=apply))
            report["blocked"] = "dream lock 由其他進程持有（dream run 進行中）；拒絕 apply"
            return report
    try:
        try:
            plan = _plan(root, now=current, retention_days=retention_days, landed_states=landed)
        except ArchiveGCError as exc:
            report.update(_empty_summary(apply=apply))
            report["error"] = str(exc)
            return report
        candidates: list[_Candidate] = plan["candidates"]
        kept: dict[str, list[_Entry]] = plan["kept"]
        by_subtree: dict[str, dict[str, int]] = {}
        for subtree in SUBTREES:
            by_subtree[subtree] = _tally(c.entry for c in candidates if c.entry.subtree == subtree)
        kept_entries = [entry for entries in kept.values() for entry in entries]
        report["scanned"] = _tally(plan["scanned"])
        report["candidates"] = {**_tally(c.entry for c in candidates), "by_subtree": by_subtree}
        report["kept"] = {
            **_tally(kept_entries),
            "by_reason": {reason: _tally(entries) for reason, entries in sorted(kept.items()) if entries},
        }
        report["unsafe_dirs"] = plan["unsafe_dirs"]
        if list_out is not None:
            _write_list(Path(list_out), candidates)
        else:
            report["candidate_paths"] = [c.entry.rel for c in candidates]
        if not apply:
            return report
        report["deleted"] = {"files": 0, "bytes": 0}
        report["missing"] = 0
        report["failed"] = []
        if plan["unsafe_dirs"]:
            report["blocked"] = ("archive 或其子樹為 symlink／非目錄（" + ", ".join(plan["unsafe_dirs"])
                                 + "）：拒絕經 symlink 刪檔")
            return report
        outcome = _delete(root, candidates) if candidates else {"deleted": [], "missing": 0, "failed": []}
        report["applied"] = True
        report["deleted"] = _tally(outcome["deleted"])
        report["missing"] = outcome["missing"]
        report["failed"] = outcome["failed"]
        return report
    finally:
        if lock_handle is not None:
            lock_handle.close()


def _empty_summary(*, apply: bool) -> dict[str, Any]:
    zero = {"files": 0, "bytes": 0}
    summary: dict[str, Any] = {
        "scanned": dict(zero),
        "candidates": {**zero, "by_subtree": {s: dict(zero) for s in SUBTREES}},
        "kept": {**zero, "by_reason": {}},
        "unsafe_dirs": [],
    }
    if apply:
        summary.update({"deleted": dict(zero), "missing": 0, "failed": []})
    return summary
