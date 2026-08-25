"""一次性回填 provenance.commit（近似）與 cites（fix 1b/1c migration，issue #136）。

仿 tags_migration.py 的 scan → dry-run report → --apply 慣例：dry-run（預設）
只回報待辦動作、不寫任何檔；--apply 走 frontmatter_io.update()（parse-equivalent、
body 逐位元不變）；apply 過的 note 下次掃描不再是候選，migration 天生冪等。

只碰 knowledge/**/*.md 且 memory_layer == "knowledge" 者；已有真實 commit
（!= "_unknown"）的 note 永不覆寫，也不下修既有 commit_source。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from paulsha_hippo.atomizer.slice_frontmatter import extract_cites
from paulsha_hippo.importer import _git
from paulsha_hippo.moc import frontmatter_io as fio

_UNKNOWN = ("", "_unknown", None)


def _archive_meta(path_value: object) -> tuple[dict | None, str]:
    """讀 provenance.path 指向的 archive queue payload；失敗一律歸類 no-archive。"""
    if not isinstance(path_value, str) or not path_value:
        return None, "no-archive"
    p = Path(path_value)
    if not p.is_file():
        return None, "no-archive"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
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
        "updated": 0, "details": [],
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
            meta, why = _archive_meta(prov.get("path"))
            if meta is not None:
                cwd, ts = meta.get("cwd"), meta.get("ended_at") or meta.get("timestamp")
                top = toplevel(cwd) if cwd else None
                if not cwd:
                    why = "no-cwd"
                elif not ts:
                    why = "no-timestamp"
                elif not top:
                    why = "not-a-repo"
                else:
                    sha = rev_before(top, ts)
                    why = "" if sha else "no-commit-before-ts"
                    if sha:
                        updates["provenance"] = {**prov, "commit": sha, "commit_source": "backfill-approx"}
                        summary["commit_candidates"] += 1
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
