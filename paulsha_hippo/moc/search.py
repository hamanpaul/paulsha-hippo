# paulshaclaw/memory/moc/search.py
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import instruction_corpus
from ..importer.config import default_projects_path, load_projects_config
from ..ledger import lifecycle
from ..ledger import retrieval_set
from ..noise import classify_noise, pool_exclude_reason
from . import frontmatter_io as fio

INDEX_WRITE_BATCH_SIZE = 100
LOGGER = logging.getLogger("paulsha_hippo.moc.search")


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


@dataclass
class BuildIndexStats:
    per_project: dict[str, ProjectIndexStats] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class SearchIndexError(Exception):
    """Raised when the search index is missing or unusable."""


def index_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "indexes" / "retrieval.db"


def _project_roots(memory_root: Path) -> dict[str, tuple[str, ...]]:
    config = load_projects_config(default_projects_path(memory_root))
    return {project.slug: project.roots for project in config.projects}


def build_index(memory_root: Path, link_weights: dict[str, int],
                doc_corpus: "object | None" = None) -> BuildIndexStats:
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    if tmp_path.exists():  # 上一次 crash 殘留的半成品
        tmp_path.unlink()
    # 先讀 projects.yaml/建語料，全部成功後才開 sqlite 連線——否則 scoping-config
    # 壞掉會洩漏連線且把整個 retrieval index 打掉（reviewer Important）。
    project_roots = _project_roots(memory_root)
    corpus_by_project: dict[str, object] = {}
    empty_corpus = instruction_corpus.corpus_for_roots(())
    stats = BuildIndexStats()

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
                fm, body = fio.read(fpath.read_text(encoding="utf-8"))
                if fm.get("memory_layer") != "knowledge":
                    continue
                sid = fm.get("slice_id")
                if not sid:
                    continue
                if pool_exclude_reason(fm) is not None:
                    continue
                project = str(fm.get("project", ""))
                project_stats = stats.per_project.setdefault(project, ProjectIndexStats())
                if classify_noise(fm, body, doc_corpus=project_corpus(project)).is_noise:
                    project_stats.excluded += 1
                    continue
                project_stats.indexed += 1
                rows.append((str(sid), project, str(fm.get("title", "")),
                             " ".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []),
                             body, str(fm.get("captured_at", "")), str(fpath)))
                if len(rows) >= INDEX_WRITE_BATCH_SIZE:
                    flush_batch(rows)
                    rows.clear()
        flush_batch(rows)
        for project, project_stats in sorted(stats.per_project.items()):
            if project_stats.exclude_rate <= 0.40:
                continue
            warning = (
                f"search index project {project}: indexed={project_stats.indexed} "
                f"excluded={project_stats.excluded} exclude_rate={project_stats.exclude_rate:.2f}"
            )
            LOGGER.warning(warning)
            stats.warnings.append(warning)
        conn.commit()
    except BaseException:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp_path, path)  # atomic：讀者永遠只看到完整 DB
    return stats


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
