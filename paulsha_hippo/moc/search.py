# paulshaclaw/memory/moc/search.py
from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

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


def index_lock_path(memory_root: Path) -> Path:
    """所有 index writer 共用的互斥鎖檔（build_index 全程持有）。"""
    return memory_root / "runtime" / "locks" / "index-rebuild.lock"


def _project_roots(memory_root: Path) -> dict[str, tuple[str, ...]]:
    config = load_projects_config(default_projects_path(memory_root))
    return {project.slug: project.roots for project in config.projects}


@contextmanager
def _index_write_lock(memory_root: Path) -> "Iterator[None]":
    """阻塞式 flock：序列化所有 build_index writer。

    global dream lock（PR-A 契約 #3，dream run 入口）擋不住 rekey / retitle
    在 dream 入口外直接呼叫 run_moc 的重建路徑；互斥必須落在所有 writer
    共用的最低層——本函式由 build_index 全程持有（掃描→temp DB→atomic
    replace→coverage 落盤為單一 critical section，DB 與 coverage 永遠成對）。
    持鎖進程死亡時 kernel 自動釋放 flock，不會殘留死鎖。
    """
    lock_path = index_lock_path(memory_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _unique_tmp(target: Path) -> Path:
    """per-invocation 唯一暫存路徑（與 target 同目錄，供 atomic os.replace）。

    固定暫存名（如 ``retrieval.db.tmp``）會讓交錯的兩個 writer 互相
    unlink 對方仍開啟的 inode，隨後的 os.replace 把對方未完成的半成品
    發布成正式檔——唯一路徑讓每個 writer 只可能發布自己寫完的完整檔，
    也是鎖被外力破壞（鎖檔遭刪）時的第二層防線。
    """
    return target.with_name(f"{target.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")


def _write_coverage(memory_root: Path, coverage: dict[str, object]) -> None:
    cov_path = coverage_path(memory_root)
    tmp = _unique_tmp(cov_path)
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

    並發安全：全程持 ``index_lock_path()`` 阻塞式 flock 序列化所有 writer
    （dream／rekey／retitle 任意呼叫路徑），temp DB 與 coverage 皆用
    per-invocation 唯一暫存路徑 + atomic replace——讀者（search()）永遠
    只看到完整舊版或完整新版索引。
    """
    with _index_write_lock(memory_root):
        return _build_index_locked(memory_root, link_weights, doc_corpus)


def _build_index_locked(memory_root: Path, link_weights: dict[str, int],
                        doc_corpus: "object | None") -> dict[str, object]:
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 持鎖中無其他活 writer：目錄裡任何 *.tmp 都是 crash 殘留的半成品
    # （含舊版固定名 retrieval.db.tmp），可安全清掉。
    for stale in path.parent.glob("*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass
    tmp_path = _unique_tmp(path)
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
