from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io


# Keep Unicode word chars (letters incl. CJK, digits, underscore); fold every run
# of other chars (punctuation, whitespace, symbols) to a single hyphen. Preserving
# CJK is what stops pure-CJK titles collapsing to "untitled" (#151).
_SLUG_STRIP = re.compile(r"[^\w]+", re.UNICODE)

# Linux 檔名上限（ext4/tmpfs NAME_MAX）：以 UTF-8 bytes 計，不是字元數（#16）。
NAME_MAX_BYTES = 255
# 直接呼叫 slugify() 的預設 byte 預算：留足 `--<slice_id>.md` 尾段與餘裕。
SLUG_MAX_BYTES_DEFAULT = 200


def _utf8_truncate(text: str, max_bytes: int) -> str:
    """把字串截到 <= max_bytes 個 UTF-8 bytes，且落在 code-point 邊界。

    UTF-8 自同步：截斷後以 errors="ignore" decode 只會丟掉尾端不完整的
    byte 序列，絕不產生半個字元。max_bytes <= 0 回空字串。
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def slugify(title: str, max_bytes: int = SLUG_MAX_BYTES_DEFAULT) -> str:
    """Convert title to a slug, preserving CJK/Unicode letters; kebab-case ASCII.

    slug 以 UTF-8 bytes 上限截斷（#16：超長 LLM title 曾組出超過 NAME_MAX
    的檔名，令 MOC reconcile rename 觸發 ENAMETOOLONG 中止整輪）。
    """
    slug = _SLUG_STRIP.sub("-", title.strip().lower()).strip("-_")
    slug = _utf8_truncate(slug, max_bytes).rstrip("-_")
    if not slug:
        slug = _utf8_truncate("untitled", max_bytes)
    return slug


def slice_filename(title: str, slice_id: str) -> str:
    """組 `<slug>--<slice_id>.md`，保證總長 <= NAME_MAX_BYTES（UTF-8 bytes）。

    `--<slice_id>.md` 尾段永不截斷（截 id 等於毀 id）；byte 預算全由 slug
    吸收。病態超長 slice_id 令尾段自身超限時這裡不丟例外——組出的名字交由
    呼叫端 rename 時 fail-soft（reconcile 記 warning 跳過，見 Task 2）。
    """
    suffix = f"--{slice_id}.md"
    budget = NAME_MAX_BYTES - len(suffix.encode("utf-8"))
    return f"{slugify(title, max_bytes=budget)}{suffix}"


def _title(fm: dict[str, Any], body: str) -> str:
    """Extract title from frontmatter, markdown heading, or fallback."""
    for key in ("title", "atom_title"):
        title = fm.get(key)
        if isinstance(title, str) and title.strip():
            return title.strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return f"{fm.get('artifact_kind', 'note')}-{fm.get('project', 'unknown')}"


def target_name(fm: dict[str, Any], body: str) -> str:
    """Generate target filename: <slug>--<slice_id>.md（UTF-8 總長 <= NAME_MAX_BYTES）"""
    return slice_filename(_title(fm, body), str(fm["slice_id"]))


def _lifecycle_path(memory_root: Path) -> Path:
    """Get path to the lifecycle ledger."""
    return memory_root / "runtime" / "ledger" / "lifecycle.jsonl"


def _append_superseded_event(
    memory_root: Path,
    slice_id: str,
    deleted_path: Path,
    kept_path: Path,
    ts: str | None = None,
) -> None:
    """Best-effort lifecycle trace for reconcile deletions.

    ``kept_path`` is the file that survives the current unlink decision before
    any follow-up rename happens. ``ts`` carries the moc pass's injected ``now``
    so the ledger event stays deterministic (no wall-clock in the moc pass).
    """
    try:
        lifecycle.append_event(
            path=_lifecycle_path(memory_root),
            record_id=slice_id,
            event_type="superseded",
            source="moc-reconcile",
            reason="moc dedup",
            actor="moc-reconcile",
            run_id=None,
            ts=ts,
            metadata={
                "deleted_path": str(deleted_path),
                "kept_path": str(kept_path),
                "schema_version": "1",
            },
        )
    except Exception:
        pass


def reconcile(memory_root: Path, now: str | None = None) -> list[str]:
    """Rename slices to <title>--<slice_id>.md and dedup by slice_id. Returns warnings.

    Fail-soft（#16）：單一壞 slice（讀檔失敗、rename 失敗如 ENAMETOOLONG、
    stat 競態）只記 warning 跳過，不中止整輪 MOC pass。

    ``now`` is the moc pass's injected logical timestamp; it is stamped on the
    dedup lifecycle traces so they stay deterministic (no wall-clock).
    """
    knowledge = memory_root / "knowledge"
    warnings: list[str] = []
    if not knowledge.exists():
        return warnings
    seen: dict[str, Path] = {}
    for path in sorted(knowledge.rglob("*.md")):
        try:
            _reconcile_one(memory_root, path, seen, warnings, now)
        except Exception as exc:  # fail-soft: 跳過毒 slice，整輪續行
            warnings.append(f"{path.name}: reconcile skipped ({exc})")
    return warnings


def _reconcile_one(
    memory_root: Path,
    path: Path,
    seen: dict[str, Path],
    warnings: list[str],
    now: str | None,
) -> None:
    """單一檔案的 rename + dedup；任何例外交由 reconcile() fail-soft 收掉。"""
    fm, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
    if fm.get("memory_layer") != "knowledge":
        return
    slice_id = fm.get("slice_id")
    if not slice_id:
        warnings.append(f"{path}: missing slice_id; skipped")
        return
    target = path.with_name(target_name(fm, body))
    if path != target:
        if target.exists():
            # Only overwrite if current file is newer
            if path.stat().st_mtime <= target.stat().st_mtime:
                _append_superseded_event(memory_root, slice_id, path, target, ts=now)
                path.unlink()
                return
            _append_superseded_event(memory_root, slice_id, target, path, ts=now)
            target.unlink()
        path.rename(target)
        path = target
    if slice_id in seen:
        other = seen[slice_id]
        if path.resolve() != other.resolve():
            older = other if other.stat().st_mtime <= path.stat().st_mtime else path
            newer = path if older is other else other
            _append_superseded_event(memory_root, slice_id, older, newer, ts=now)
            older.unlink()
            seen[slice_id] = newer
            warnings.append(f"duplicate slice_id {slice_id}; kept {newer.name}")
    else:
        seen[slice_id] = path
