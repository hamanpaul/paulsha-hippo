"""既存 session 狀態 note 一次性降層 episodic（fix 4 migration，issue #136）。

仿 provenance_backfill.py/tags_migration.py 的 scan -> dry-run report -> --apply
慣例：dry-run（`apply=False`，預設）只回報候選、不寫任何檔；`--apply` 走
`frontmatter_io.update()`（parse-equivalent、body 逐位元不變、atomic write）；
已降層過的 note（memory_layer != knowledge）下次掃描不再是候選，migration 天生
冪等。分類邏輯完全借用 `noise.episodic_reason`（Task 13 的唯一分類器），本檔
不新增啟發式。

不刪檔：只把 `memory_layer` 從 `knowledge` 降成 `episodic`，MOC index 已依
`memory_layer` 排除非 knowledge 層，episodic note 自然退出檢索池但仍在磁碟上。
`--revert` 是唯一反向操作，只碰 `memory_layer == "episodic"` 且該 slice_id 相符
的那一個 note，還原 `memory_layer` 並移除 `episodic_reason`；apply 後
revert，檔案須與原始逐位元相同。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.noise import episodic_reason


def _lifecycle_path(root: Path) -> Path:
    return root / "runtime" / "ledger" / "lifecycle.jsonl"


def _iter_notes(root: Path, warnings: list[str]) -> Iterator[tuple[Path, dict, str]]:
    """Yield ``(path, frontmatter, body)`` for every note under ``<root>/knowledge``.

    Mirrors provenance_backfill.run's scan: ``-moc.md`` index files are
    unconditionally skipped (never a standalone knowledge atom); unreadable /
    non-UTF-8 files are skipped and recorded into ``warnings`` (same shape as
    provenance_backfill), never raised.
    """
    knowledge = root / "knowledge"
    if not knowledge.is_dir():
        return
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Failed to read {path}: {exc}")
            continue
        yield path, fm, body


def run(memory_root: Path | str, *, apply: bool, now: str,
        project: str | None = None) -> tuple[dict, list[str]]:
    """掃 ``<root>/knowledge`` 下 ``memory_layer == knowledge`` 的 note，跑
    ``noise.episodic_reason`` 分類；命中的候選記進 ``summary["details"]``。
    ``apply=True`` 才實際降層並寫入 lifecycle ``archived`` 事件；``apply=False``
    （預設，dry-run）只回報、保證不寫任何檔。回傳 ``(summary, warnings)``。
    """
    root = Path(memory_root)
    summary = {"scanned": 0, "pending": 0, "updated": 0, "details": []}
    warnings: list[str] = []
    for path, fm, body in _iter_notes(root, warnings):
        if fm.get("memory_layer") != "knowledge":
            continue
        if project and fm.get("project") != project:
            continue
        summary["scanned"] += 1
        reason = episodic_reason(fm.get("title"), body)
        if not reason:
            continue
        summary["pending"] += 1
        summary["details"].append({
            "path": str(path.relative_to(root)),
            "title": str(fm.get("title", "")),
            "reason": reason,
        })
        if apply:
            try:
                fio.update(path, {"memory_layer": "episodic", "episodic_reason": reason})
                lifecycle.append_event(
                    path=_lifecycle_path(root), record_id=str(fm.get("slice_id", "")),
                    event_type="archived", source="mark-episodic", reason="episodic",
                    actor="hippo", ts=now,
                )
                summary["updated"] += 1
            except Exception as exc:
                warnings.append(f"Failed to update {path}: {exc}")
    return summary, warnings


def revert(memory_root: Path | str, slice_id: str, *, now: str) -> bool:
    """還原單一 slice：``episodic`` -> ``knowledge``，移除 ``episodic_reason``，
    寫 lifecycle ``restored`` 事件。只碰 ``memory_layer == "episodic"`` 且
    ``slice_id`` 相符的那一個 note；找不到回傳 ``False``（CLI 依此決定 exit
    code），是 ``run()`` apply 動作的精確反向。
    """
    root = Path(memory_root)
    warnings: list[str] = []
    for path, fm, _body in _iter_notes(root, warnings):
        if fm.get("slice_id") != slice_id or fm.get("memory_layer") != "episodic":
            continue
        fio.update(path, {"memory_layer": "knowledge"}, remove=("episodic_reason",))
        lifecycle.append_event(
            path=_lifecycle_path(root), record_id=slice_id, event_type="restored",
            source="mark-episodic", reason="revert", actor="hippo", ts=now,
        )
        return True
    return False
