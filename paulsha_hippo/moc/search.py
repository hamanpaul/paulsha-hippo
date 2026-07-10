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
    """索引成功發布後派生的六欄 coverage JSON（便利輸出）。

    權威 coverage 存於 retrieval.db 內的 ``coverage`` 表（read_coverage()），
    與索引由同一次 os.replace 原子發布；本檔為發布後的派生輸出，衍生失敗
    只記 warning、可能 stale——讀取端（cli.run_index_verify）以 DB 為準，
    僅對沒有 coverage 表的舊版 DB 退回讀本檔。
    """
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
    共用的最低層——本函式由 build_index 全程持有（掃描→temp DB（含
    coverage 表）→單次 atomic replace 為單一 critical section，索引與
    coverage 永遠成對；coverage JSON 為發布後派生輸出）。
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
    """派生 coverage JSON（成功發布後的便利輸出；權威版在 DB coverage 表）。"""
    cov_path = coverage_path(memory_root)
    tmp = _unique_tmp(cov_path)
    try:
        tmp.write_text(
            json.dumps(coverage, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, cov_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _persist_coverage_table(conn: sqlite3.Connection,
                            coverage: dict[str, object]) -> None:
    """coverage 併入 temp DB 的 ``coverage`` 表（單一 JSON payload 列）。

    必須在 os.replace **之前**執行：索引與 coverage 由同一次 replace 原子
    發布，這裡任何失敗都留在 temp 檔內、舊 DB 與舊 coverage 完整保留——
    關閉「replace 已換新 DB、coverage 尚未落盤」的半發布視窗。
    """
    conn.execute("CREATE TABLE coverage (payload TEXT NOT NULL)")
    conn.execute("INSERT INTO coverage VALUES (?)",
                 (json.dumps(coverage, ensure_ascii=False, sort_keys=True),))


def read_coverage(memory_root: Path) -> "dict[str, object] | None":
    """讀已發布索引 DB 內的六欄 coverage（權威來源；與索引同次 replace 發布）。

    回傳 None 表示不可得：索引不存在、DB 無 coverage 表（本表出現前的
    舊版 DB）或 payload 不可解析——讀取端自行決定 fallback（派生 JSON）
    或回報缺失。
    """
    path = index_path(memory_root)
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT payload FROM coverage").fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def build_index(memory_root: Path, link_weights: dict[str, int],
                doc_corpus: "object | None" = None) -> dict[str, object]:
    """建 retrieval index（temp DB + atomic replace）並回傳 coverage 報表。

    回傳 dict（跨批次契約 #6）：六欄 ``scanned / invalid_frontmatter /
    pool_excluded / noise_excluded / eligible / indexed``（excluded 兩欄為
    {reason: count}），外加 repo 內部鍵 ``per_project``（exclude-rate 觀測）
    與 ``warnings``。同一份六欄 coverage 先寫進 temp DB 的 ``coverage`` 表、
    與索引由**單次** os.replace 原子發布（read_coverage() 為 `hippo index
    verify` 三方對賬的權威來源，census.py）；發布成功後另派生
    ``coverage_path()`` JSON，衍生失敗僅記 warning、不影響已發布的索引。

    掃描檔案唯一去向（census._fate 以刻意雙寫的獨立規則對齊——分支順序與
    reason 字串一字不差；規則有意圖內變更時兩邊必須同步改，見 census.py
    模組 docstring）：讀檔失敗/壞 frontmatter
    → invalid_frontmatter；memory_layer != knowledge →
    pool_excluded[non-knowledge-layer:<layer>]；缺 slice_id →
    invalid_frontmatter；pool_exclude_reason → pool_excluded[<reason>]；
    classify_noise → noise_excluded[<reason>]；slice_id 已被本輪較早的
    eligible 檔佔用（naming dedup fail-soft 後磁碟殘留重複）→
    pool_excluded[duplicate-slice-id-on-disk]（先到先贏，fail-soft 記
    warning，不中止整批；census 的鏡像規則在 reconcile_index 迴圈）；
    其餘 eligible（=實際入索引）。

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
        seen_slice_ids: set[str] = set()
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
                sid_str = str(sid)
                if sid_str in seen_slice_ids:
                    # 磁碟上同 slice_id 多檔（naming.reconcile dedup fail-soft
                    # 後的合法殘留狀態）：slice_meta 的 PK 會讓 INSERT 丟
                    # IntegrityError 炸掉整批、連健康 slices 都退回舊索引。
                    # 先到先贏：後到者歸 pool_excluded 記 warning，整批續行。
                    reason = "duplicate-slice-id-on-disk"
                    pool_excluded[reason] = pool_excluded.get(reason, 0) + 1
                    warnings.append(
                        f"index: duplicate slice_id on disk {sid_str}; "
                        f"excluded {fpath.name} (kept first occurrence)")
                    continue
                seen_slice_ids.add(sid_str)
                coverage["eligible"] += 1
                project_stats.indexed += 1
                rows.append((sid_str, project, str(fm.get("title", "")),
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
        # coverage 併入同一顆 temp DB：與索引同一次 os.replace 原子發布。
        # 若 coverage 寫在 replace 之後，寫入失敗（ENOSPC）或程序在兩步間
        # 終止會留下「新 DB＋舊/缺 coverage」且整體回報失敗——違反「建索引
        # 失敗時舊 DB 完整保留」契約（Codex 複驗 blocking）。
        _persist_coverage_table(conn, coverage)
        conn.commit()
    except BaseException:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp_path, path)  # atomic：索引＋coverage 一次發布，讀者永遠看到完整成對
    try:
        _write_coverage(memory_root, coverage)  # 派生 JSON；權威版已隨 DB 發布
    except OSError as exc:
        message = (f"index: coverage json derivation failed ({exc}); "
                   "index published, authoritative coverage in retrieval.db")
        LOGGER.warning(message)
        warnings.append(message)
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
