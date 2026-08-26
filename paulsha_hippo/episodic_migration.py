"""既存 session 狀態 note 一次性降層 episodic（fix 4 migration，issue #136）。

仿 provenance_backfill.py/tags_migration.py 的 scan -> dry-run report -> --apply
慣例：dry-run（`apply=False`，預設）只回報候選、不寫任何檔；`--apply` 走
`frontmatter_io.update()`（parse-equivalent、body 逐位元不變、atomic write）；
已降層過的 note（memory_layer != knowledge）下次掃描不再是候選，migration 天生
冪等。分類邏輯完全借用 `noise.episodic_reason`（Task 13 的唯一分類器），本檔
不新增啟發式。

不刪檔：只把 `memory_layer` 從 `knowledge` 降成 `episodic`，MOC index 已依
`memory_layer` 排除非 knowledge 層，episodic note 自然退出檢索池但仍在磁碟上。

`--revert` 是唯一反向操作。`episodic_reason` 不能用來分辨「這個 note 是被本
migration 降層的」還是「Task 13 的 atomizer/pipeline.py 在 publish 時就直接
寫成 episodic 的」——兩者寫出的形狀完全一樣（`memory_layer: episodic` +
`episodic_reason`，且 `episodic_reason` 是每個 episodic note 的 schema 必填
欄位，見 atomizer/slice_frontmatter.py::validate）。所以 `--apply` 額外寫一個
本 migration 專屬的標記 `episodic_demoted_by: mark-episodic`；`--revert` 只碰
`memory_layer == "episodic"` 且該 slice_id 相符**且帶有這個標記**的那一個
note，還原 `memory_layer` 並移除 `episodic_reason` 與 `episodic_demoted_by`；
apply 後 revert，檔案須與原始逐位元相同。slice_id 存在但沒有標記（例如
pipeline 直接降的層）→ 不 revert，回傳的 message 標示 `not-migrated`，CLI 走
今天「not-found」的 exit 1 路徑（review round 1 #1）。

`episodic_demoted_by` 是額外欄位，不在既有 schema 的必填/允許清單裡，但不會
被任何既存路徑拒絕：`slice_frontmatter.validate`（經
`lib/lifecycle/schema.py::validate_frontmatter`）只檢查
`REQUIRED_FRONTMATTER_FIELDS` 是否存在與少數欄位的型別/值域，從不檢查
frontmatter 有沒有「不認識」的多餘 key；而且這條 validate 路徑本來就不會套用
在既存檔案上——`run()`/`revert()` 本身不呼叫它，唯一在既存檔案上呼叫
`slice_frontmatter.validate` 的是 `replay/bundle.py`（bundle 匯出時），同樣不
檢查多餘 key。讀 frontmatter 的其他既存路徑都只認自己關心的欄位：
`moc/search.py`（`classify()`，只讀 `memory_layer` 等已知欄位）、
`moc/census.py`（`_FM_FIELD` 這個 regex 只認頂層 `slice_id`/`memory_layer`
兩個 key，其餘欄位對它完全不可見）、`janitor/record_source.py`
（`_build_record()` 只讀 `memory_layer` 判斷 knowledge 層，其餘用
`data.get(...)`）。三者都不會因為多一個 `episodic_demoted_by` 而出錯或改變
行為。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.noise import episodic_reason

# --apply 寫入的專屬標記；--revert 靠它跟 Task 13 pipeline 在 publish 時就寫成
# episodic 的 note 區分開（review round 1 #1）。
DEMOTED_BY = "mark-episodic"


def _lifecycle_path(root: Path) -> Path:
    return root / "runtime" / "ledger" / "lifecycle.jsonl"


def _iter_notes(root: Path, warnings: list[str],
                 summary: dict | None = None) -> Iterator[tuple[Path, dict, str]]:
    """Yield ``(path, frontmatter, body)`` for every note under ``<root>/knowledge``.

    Mirrors provenance_backfill.run's scan: ``-moc.md`` index files are
    unconditionally skipped (never a standalone knowledge atom) — when
    ``summary`` is given, each skip increments ``summary["skipped_moc"]`` so
    the caller's report accounts for them (review round 1 #3); unreadable /
    non-UTF-8 files are skipped and recorded into ``warnings`` (same shape as
    provenance_backfill), never raised.
    """
    knowledge = root / "knowledge"
    if not knowledge.is_dir():
        return
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            if summary is not None:
                summary["skipped_moc"] = summary.get("skipped_moc", 0) + 1
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
    summary = {"scanned": 0, "pending": 0, "updated": 0, "skipped_moc": 0, "details": []}
    warnings: list[str] = []
    for path, fm, body in _iter_notes(root, warnings, summary):
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
                fio.update(path, {
                    "memory_layer": "episodic",
                    "episodic_reason": reason,
                    "episodic_demoted_by": DEMOTED_BY,
                })
            except Exception as exc:
                warnings.append(f"Failed to update {path}: {exc}")
                continue
            # review round 1 #2: the write already landed on disk at this
            # point — count it now, independent of whether the ledger append
            # below succeeds. A ledger failure must not make an already
            # demoted, already revertible note vanish from `updated`.
            summary["updated"] += 1
            try:
                lifecycle.append_event(
                    path=_lifecycle_path(root), record_id=str(fm.get("slice_id", "")),
                    event_type="archived", source="mark-episodic", reason="episodic",
                    actor="hippo", ts=now,
                )
            except Exception as exc:
                warnings.append(f"ledger append failed for {path}: {exc}")
    return summary, warnings


def revert(memory_root: Path | str, slice_id: str, *, now: str) -> tuple[bool, str]:
    """還原單一 slice：``episodic`` -> ``knowledge``，移除 ``episodic_reason``
    與 ``episodic_demoted_by``，寫 lifecycle ``restored`` 事件。

    只碰 ``memory_layer == "episodic"`` 且 ``slice_id`` 相符**且帶有
    ``episodic_demoted_by == "mark-episodic"`` 標記**的那一個 note——沒有這個
    標記就代表這個 note 是 Task 13 pipeline 在 publish 時直接寫成 episodic
    的，不是本 migration 降的層，不該被 revert（review round 1 #1）。是
    ``run()`` apply 動作的精確反向。

    回傳 ``(reverted, message)``：
    - 成功：``(True, "")``。
    - 找不到任何 slice_id 相符的 note：``(False, "not-found: ...")``。
    - slice_id 相符但不是本 migration 降層的（``memory_layer`` 不是
      ``episodic``，或沒有 ``episodic_demoted_by`` 標記）：
      ``(False, "not-migrated: ...")``。

    CLI 對這兩種失敗都走今天「not-found」的 exit 1 路徑，只是印出的 message
    不同，讓使用者能分辨「這個 slice 根本不存在」跟「這個 slice 是 episodic
    但不是這支 migration 降的層、不能用這個指令復原」。
    """
    root = Path(memory_root)
    warnings: list[str] = []
    for path, fm, _body in _iter_notes(root, warnings):
        if fm.get("slice_id") != slice_id:
            continue
        if fm.get("memory_layer") == "episodic" and fm.get("episodic_demoted_by") == DEMOTED_BY:
            fio.update(path, {"memory_layer": "knowledge"},
                       remove=("episodic_reason", "episodic_demoted_by"))
            lifecycle.append_event(
                path=_lifecycle_path(root), record_id=slice_id, event_type="restored",
                source="mark-episodic", reason="revert", actor="hippo", ts=now,
            )
            return True, ""
        return False, (
            f"not-migrated: slice {slice_id!r} has memory_layer="
            f"{fm.get('memory_layer')!r} without an episodic_demoted_by="
            f"{DEMOTED_BY!r} marker (not demoted by mark-episodic --apply); "
            "not reverted"
        )
    return False, f"not-found: no note with slice_id={slice_id!r} under {root / 'knowledge'}"
