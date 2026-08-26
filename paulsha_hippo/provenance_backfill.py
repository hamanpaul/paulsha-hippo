"""一次性回填 provenance.commit（近似）與 cites（fix 1b/1c migration，issue #136）。

仿 tags_migration.py 的 scan → dry-run report → --apply 慣例：dry-run（預設）
只回報待辦動作、不寫任何檔；--apply 走 frontmatter_io.update()（parse-equivalent、
body 逐位元不變）；apply 過的 note 下次掃描不再是候選，migration 天生冪等。

只碰 `<root>/knowledge` 目錄下的 `*.md`（`memory_layer == "knowledge"`）者；已有真實 commit
（!= "_unknown"）的 note 永不覆寫，也不下修既有 commit_source。

commit_reasons 的完整原因集合：no-archive（path 缺/不可讀/非 JSON dict）、
path-escape（path 解析後不在 <memory_root>/archive/queue 之內，比照 recovery.py
的 archive 圍籬，issue #136 review round 1 #1）、no-cwd、no-timestamp（archive
payload 的 ended_at/timestamp 與 note 自身 captured_at 三者皆缺/空）、
not-a-repo、no-commit-before-ts。ts 來源優先序 ended_at > timestamp >
note.captured_at（回退，owner-approved fallback, 2026-08-25）；哪個來源實際
命中候選記在 summary["ts_source"]。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Callable

from paulsha_hippo.atomizer.slice_frontmatter import extract_cites
from paulsha_hippo.importer import _git
from paulsha_hippo.moc import frontmatter_io as fio

_UNKNOWN = ("", "_unknown", None)


def _coerce_captured_at(value: object) -> str | None:
    """把 note 自身 frontmatter 的 captured_at 轉成可餵給 git 的字串 ts。

    frontmatter_io.read() 用的是純 yaml.safe_load：沒加引號的
    ``captured_at: 2026-08-17T03:32:41Z`` 會被 YAML 隱式解析成
    datetime.datetime，不是 str；舊版單純 isinstance(..., str) 守門會把這種
    常見寫法誤判成缺 timestamp。比照 skillopt/valset.py:107
    ``str(... or "_unknown")`` 的型別容忍慣例：datetime/date 用 .isoformat()，
    其餘非空值一律 str()，None/空字串維持不回退。
    """
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value.isoformat()
    s = str(value)
    return s if s.strip() else None


def _archive_meta(path_value: object, root: Path) -> tuple[dict | None, str]:
    """讀 provenance.path 指向的 archive queue payload。

    path 須解析落在 ``<root>/archive/queue`` 之內（比照 recovery.py:93-101 /
    :130-137 的圍籬寫法）；逃逸一律歸類 path-escape、其餘失敗（缺檔/不可讀/
    非 JSON dict）歸類 no-archive。
    """
    if not isinstance(path_value, str) or not path_value:
        return None, "no-archive"
    p = Path(path_value)
    if not p.is_file():
        return None, "no-archive"
    archive_root = (root / "archive" / "queue").resolve()
    resolved = p.resolve()
    if not resolved.is_relative_to(archive_root):
        return None, "path-escape"
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "no-archive"
    if not isinstance(data, dict):
        return None, "no-archive"
    return data, ""


def run(memory_root: Path | str, *, apply: bool = False, project: str | None = None,
        rev_before: Callable = _git.git_rev_before, toplevel: Callable = _git.git_toplevel) -> tuple[dict, list[str]]:
    """掃 knowledge slices，回填近似 commit 與 cites。回傳 (summary_dict, warnings_list)。"""
    root = Path(memory_root)
    knowledge = root / "knowledge"
    summary = {
        "scanned": 0, "commit_candidates": 0, "commit_reasons": {}, "cites_candidates": 0,
        "updated": 0, "details": [], "ts_source": {},
    }
    warnings: list[str] = []
    if not knowledge.is_dir():
        return summary, warnings

    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Failed to read {path}: {exc}")
            continue
        if fm.get("memory_layer") != "knowledge" or (project and fm.get("project") != project):
            continue

        summary["scanned"] += 1
        updates: dict = {}
        prov = dict(fm.get("provenance") or {}) if isinstance(fm.get("provenance"), dict) else {}

        if prov.get("commit") in _UNKNOWN:
            meta, why = _archive_meta(prov.get("path"), root)
            if meta is not None:
                cwd = meta.get("cwd")
                captured_at = _coerce_captured_at(fm.get("captured_at"))
                ts, ts_source = None, None
                if meta.get("ended_at"):
                    ts, ts_source = meta.get("ended_at"), "ended_at"
                elif meta.get("timestamp"):
                    ts, ts_source = meta.get("timestamp"), "timestamp"
                elif captured_at:
                    ts, ts_source = captured_at, "captured_at"
                if not cwd:
                    why = "no-cwd"
                elif not ts:
                    why = "no-timestamp"
                else:
                    # perf: defer toplevel until ts known — no point spawning
                    # `git rev-parse --show-toplevel` for a note we're about to
                    # skip as no-timestamp anyway.
                    top = toplevel(cwd)
                    if not top:
                        why = "not-a-repo"
                    else:
                        sha = rev_before(top, ts)
                        why = "" if sha else "no-commit-before-ts"
                        if sha:
                            updates["provenance"] = {**prov, "commit": sha, "commit_source": "backfill-approx"}
                            summary["commit_candidates"] += 1
                            summary["ts_source"][ts_source] = summary["ts_source"].get(ts_source, 0) + 1
            if why:
                summary["commit_reasons"][why] = summary["commit_reasons"].get(why, 0) + 1

        cites = extract_cites(body)
        if cites != (fm.get("cites") or []):
            updates["cites"] = cites
            summary["cites_candidates"] += 1

        if not updates:
            continue

        summary["details"].append({
            "path": str(path.relative_to(root)),
            "commit": updates.get("provenance", {}).get("commit"),
            "cites_count": len(updates.get("cites", [])),
        })

        if apply:
            try:
                fio.update(path, updates)
                summary["updated"] += 1
            except Exception as exc:
                warnings.append(f"Failed to update {path}: {exc}")

    return summary, warnings
