# paulshaclaw/memory/moc/search.py
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import instruction_corpus
from ..importer.config import default_projects_path, load_projects_config
from ..ledger import lifecycle
from ..ledger import retrieval_set
from ..noise import classify_noise, pool_exclude_reason
from . import frontmatter_io as fio

INDEX_WRITE_BATCH_SIZE = 100
LOGGER = logging.getLogger("paulsha_hippo.moc.search")

# 跨批次契約 #6：build_index() 回傳 dict 的六個 coverage 鍵。
COVERAGE_KEYS = ("scanned", "invalid_frontmatter", "pool_excluded",
                 "noise_excluded", "eligible", "indexed")


@dataclass
class ProjectIndexStats:
    indexed: int = 0
    excluded: int = 0

    @property
    def exclude_rate(self) -> float:
        total = self.indexed + self.excluded
        if total == 0:
            return 0.0
        return self.excluded / total


class SearchIndexError(Exception):
    """Raised when the search index is missing or unusable."""


def index_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "indexes" / "retrieval.db"


def coverage_path(memory_root: Path) -> Path:
    """build_index() 成功後原子落盤的六欄 coverage 報表（三方對賬的比對基準）。"""
    return memory_root / "runtime" / "indexes" / "retrieval.coverage.json"


def _project_roots(memory_root: Path) -> dict[str, tuple[str, ...]]:
    config = load_projects_config(default_projects_path(memory_root))
    return {project.slug: project.roots for project in config.projects}


def _write_coverage(memory_root: Path, coverage: dict[str, object]) -> None:
    cov_path = coverage_path(memory_root)
    tmp = cov_path.with_name(cov_path.name + ".tmp")
    tmp.write_text(
        json.dumps(coverage, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, cov_path)


def build_index(memory_root: Path, link_weights: dict[str, int],
                doc_corpus: "object | None" = None) -> dict[str, object]:
    """建 retrieval index（temp DB + atomic replace）並回傳 coverage 報表。

    回傳 dict（跨批次契約 #6）：六欄 ``scanned / invalid_frontmatter /
    pool_excluded / noise_excluded / eligible / indexed``（excluded 兩欄為
    {reason: count}），外加 repo 內部鍵 ``per_project``（exclude-rate 觀測）
    與 ``warnings``。同一份六欄 coverage 會原子落盤到 ``coverage_path()``，
    供 `hippo index verify` 三方對賬（census.py）。

    掃描檔案唯一去向（census._fate 以刻意雙寫的獨立規則對齊——分支順序與
    reason 字串一字不差；規則有意圖內變更時兩邊必須同步改，見 census.py
    模組 docstring）：讀檔失敗/壞 frontmatter
    → invalid_frontmatter；memory_layer != knowledge →
    pool_excluded[non-knowledge-layer:<layer>]；缺 slice_id →
    invalid_frontmatter；pool_exclude_reason → pool_excluded[<reason>]；
    classify_noise → noise_excluded[<reason>]；其餘 eligible（=實際入索引）。
    """
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    if tmp_path.exists():  # 上一次 crash 殘留的半成品
        tmp_path.unlink()
    # 先讀 projects.yaml/建語料，全部成功後才開 sqlite 連線——否則 scoping-config
    # 壞掉會洩漏連線（reviewer Important）。
    project_roots = _project_roots(memory_root)
    corpus_by_project: dict[str, object] = {}
    empty_corpus = instruction_corpus.corpus_for_roots(())
    per_project: dict[str, ProjectIndexStats] = {}
    warnings: list[str] = []
    pool_excluded: dict[str, int] = {}
    noise_excluded: dict[str, int] = {}
    coverage: dict[str, object] = {
        "scanned": 0, "invalid_frontmatter": 0, "pool_excluded": pool_excluded,
        "noise_excluded": noise_excluded, "eligible": 0, "indexed": 0,
    }

    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("CREATE VIRTUAL TABLE slices_fts USING fts5("
                     "slice_id UNINDEXED, project, title, tags, body, tokenize='unicode61')")
        conn.execute("CREATE TABLE slice_meta (slice_id TEXT PRIMARY KEY, project TEXT, "
                     "captured_at TEXT, active INTEGER, link_weight INTEGER, path TEXT)")
        knowledge = memory_root / "knowledge"
        events = lifecycle.read_events(memory_root)

        def flush_batch(rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
            if not rows:
                return
            active = set(
                retrieval_set.active_records(
                    memory_root,
                    [row[0] for row in rows],
                    events=events,
                )
            )
            conn.executemany(
                "INSERT INTO slices_fts VALUES (?,?,?,?,?)",
                [(sid, project, title, tags, body)
                 for sid, project, title, tags, body, _captured_at, _path in rows],
            )
            conn.executemany(
                "INSERT INTO slice_meta VALUES (?,?,?,?,?,?)",
                [
                    (sid, project, captured_at, 1 if sid in active else 0,
                     link_weights.get(sid, 0), fpath)
                    for sid, project, _title, _tags, _body, captured_at, fpath in rows
                ],
            )

        def project_corpus(project: str) -> object:
            cached = corpus_by_project.get(project)
            if cached is not None:
                return cached
            if project in project_roots:
                corpus = instruction_corpus.corpus_for_roots(project_roots[project])
            elif doc_corpus is not None and not project_roots:
                corpus = doc_corpus
            else:
                corpus = empty_corpus
            corpus_by_project[project] = corpus
            return corpus

        rows: list[tuple[str, str, str, str, str, str, str]] = []
        if knowledge.exists():
            for fpath in sorted(knowledge.rglob("*.md")):
                coverage["scanned"] += 1
                try:
                    fm, body = fio.read(fpath.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    coverage["invalid_frontmatter"] += 1
                    warnings.append(f"index: unreadable {fpath.name} ({exc})")
                    continue
                if not fm:
                    coverage["invalid_frontmatter"] += 1
                    continue
                layer = fm.get("memory_layer")
                if layer != "knowledge":
                    reason = f"non-knowledge-layer:{layer or 'none'}"
                    pool_excluded[reason] = pool_excluded.get(reason, 0) + 1
                    continue
                sid = fm.get("slice_id")
                if not sid:
                    coverage["invalid_frontmatter"] += 1
                    continue
                reason = pool_exclude_reason(fm)
                if reason is not None:
                    pool_excluded[reason] = pool_excluded.get(reason, 0) + 1
                    continue
                project = str(fm.get("project", ""))
                project_stats = per_project.setdefault(project, ProjectIndexStats())
                verdict = classify_noise(fm, body, doc_corpus=project_corpus(project))
                if verdict.is_noise:
                    noise_excluded[verdict.reason] = noise_excluded.get(verdict.reason, 0) + 1
                    project_stats.excluded += 1
                    continue
                coverage["eligible"] += 1
                project_stats.indexed += 1
                rows.append((str(sid), project, str(fm.get("title", "")),
                             " ".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []),
                             body, str(fm.get("captured_at", "")), str(fpath)))
                if len(rows) >= INDEX_WRITE_BATCH_SIZE:
                    flush_batch(rows)
                    rows.clear()
        flush_batch(rows)
        for project, project_stats in sorted(per_project.items()):
            if project_stats.exclude_rate <= 0.40:
                continue
            warning = (
                f"search index project {project}: indexed={project_stats.indexed} "
                f"excluded={project_stats.excluded} exclude_rate={project_stats.exclude_rate:.2f}"
            )
            LOGGER.warning(warning)
            warnings.append(warning)
        conn.commit()
        # indexed 從 DB 讀回而非回抄迴圈計數——報表對 DB 的最小自檢
        coverage["indexed"] = conn.execute(
            "SELECT COUNT(*) FROM slice_meta").fetchone()[0]
    except BaseException:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp_path, path)  # atomic：讀者永遠只看到完整 DB
    _write_coverage(memory_root, coverage)
    return {
        **coverage,
        "per_project": {
            project: {"indexed": stats.indexed, "excluded": stats.excluded,
                      "exclude_rate": stats.exclude_rate}
            for project, stats in sorted(per_project.items())
        },
        "warnings": warnings,
    }


def search(memory_root: Path, query: str, *, project: str | None, limit: int,
           include_decayed: bool) -> list[dict]:
    path = index_path(memory_root)
    if not path.exists():
        raise SearchIndexError("search index not built; run the dream/moc pass first")
    conn = sqlite3.connect(path)
    try:
        sql = ("SELECT f.slice_id, m.project, f.title, bm25(slices_fts) AS bm, "
               "m.link_weight, m.active, m.path "
               "FROM slices_fts f JOIN slice_meta m ON m.slice_id = f.slice_id "
               "WHERE slices_fts MATCH ?")
        params: list[object] = [query]
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        if not include_decayed:
            sql += " AND m.active = 1"
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise SearchIndexError(f"search failed: {exc}") from exc
    finally:
        conn.close()
    # rank: lower bm25 is better; add link_weight boost. (recency omitted for determinism in MVP test.)
    ranked = sorted(rows, key=lambda r: (r[3] - 0.1 * (r[4] or 0)))
    return [{"slice_id": r[0], "project": r[1], "title": r[2], "score": r[3], "path": r[6]}
            for r in ranked[:limit]]
