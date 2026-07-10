"""三方對賬（#16）：filesystem census × coverage 報表 × index DB 反查。

build_index() 的 coverage 報表出自它自己的掃描迴圈，單獨看是同源自證。
本模組提供分離實作的獨立驗證面（spec §3.2 驗收「防同源自證」）：

- ``filesystem_census()``：純檔案枚舉（os.walk + 最小 line-based 欄位抽取，
  不走 rglob、不走 yaml），建立磁碟上的檔案／ID 全集。
- 獨立 fate pass（``_fate``）：另一條迴圈對每個 census 檔案指派唯一去向
  （invalid / pool-excluded(reason) / noise-excluded(reason) / eligible）。
  **分類規則刻意雙寫**：pool／noise eligibility 規則在本模組內獨立重寫
  （``_census_pool_reason`` / ``_census_noise_reason``），不 import noise.py
  的生產 classifier、也不用 instruction_corpus 的語料建構 helper——共用
  生產分類邏輯會把 classifier 的 bug 同時複製到兩邊，eligible == indexed
  照樣通過（同源自證）。與 indexing 路徑共用的只有原語：檔案枚舉
  （os.walk／discover_instruction_docs）、fio.read 低階 frontmatter 解析、
  sqlite 查詢、projects.yaml 設定讀取。
- ``indexed_ids()``：從建好的 retrieval.db 反查 slice_meta。

census 與 build_index 的分類邏輯刻意雙寫：兩邊規則若有意圖內的變更，必須
同步改兩份；漏改任何一邊時 reconcile 的分佈比對會顯性回報不一致
（ClassifierDivergenceTests 以單邊注入 bug 驗證此 tripwire）。

三方一致（census 檔數 == coverage.scanned、fate 分佈 == coverage 各欄、
eligible ID 集 == indexed ID 集）才算強不變量 ``indexed IDs == eligible IDs``
成立；全部動態計算，不寫死數字。
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..importer.config import default_projects_path, load_projects_config
from ..instruction_corpus import discover_instruction_docs
from . import frontmatter_io as fio
from .search import SearchIndexError, index_path

# 只認 frontmatter 區塊內「頂格」的兩個身份欄位；值去頭尾引號。
_FM_FIELD = re.compile(r"^(slice_id|memory_layer):\s*(.+?)\s*$")


@dataclass(frozen=True)
class CensusEntry:
    path: str
    slice_id: "str | None"
    memory_layer: "str | None"


@dataclass
class ReconcileResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    census_files: int = 0
    eligible_ids: set[str] = field(default_factory=set)
    indexed_ids: set[str] = field(default_factory=set)


def _minimal_fields(text: str) -> "tuple[str | None, str | None]":
    """line-based frontmatter 欄位抽取：只認頂層 slice_id / memory_layer。

    刻意不用 yaml——census 只需要身份欄位，且實作必須與 fio.read 分離；
    若兩者對真實資料產生分歧，reconcile 會以 problems 顯性回報。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        matched = _FM_FIELD.match(line)
        if matched:
            fields.setdefault(matched.group(1), matched.group(2).strip().strip("'\""))
    return fields.get("slice_id"), fields.get("memory_layer")


def filesystem_census(memory_root: Path) -> list[CensusEntry]:
    """純檔案枚舉：os.walk（非 rglob）列出 knowledge 樹下全部 *.md 與其身份欄位。"""
    knowledge = memory_root / "knowledge"
    entries: list[CensusEntry] = []
    if not knowledge.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(knowledge):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.endswith(".md"):
                continue
            fpath = Path(dirpath) / fname
            try:
                text = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                entries.append(CensusEntry(str(fpath), None, None))
                continue
            sid, layer = _minimal_fields(text)
            entries.append(CensusEntry(str(fpath), sid, layer))
    return entries


def indexed_ids(memory_root: Path) -> set[str]:
    """DB 反查：完成後的 retrieval.db 內實際存在的 slice_id 全集。"""
    path = index_path(memory_root)
    if not path.exists():
        raise SearchIndexError("search index not built; run the dream/moc pass first")
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT slice_id FROM slice_meta")}
    finally:
        conn.close()


# --- census 本地分類規則（刻意雙寫；勿改回 import 生產 classifier）-----------
# 語義基準：noise.py 的 pool／noise 判定與 generic-title 規則、instruction_corpus
# 的語料正規化。兩邊規則有意圖內的變更必須同步改兩份；漏改任何一邊時
# reconcile 的分佈比對會顯性回報（tests/test_moc_census.py ClassifierDivergenceTests）。

_IMPORTER_ECHO_HEADINGS = {
    f"## {name}": name
    for name in ("CWD", "Source", "Prompts", "Touched files", "Referenced artifacts")
}
_SESSION_META_LINE = re.compile(r"^#{1,6}\s+Session\s+(?:Metadata|Information)\b")
_HEADING_LINE = re.compile(r"^#{1,6}\s")
_LIST_ITEM = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")
_PLACEHOLDER_PHRASES = ("(無內容)", "尚未收到您的具體需求", "目前尚未收到")
_BARE_PLACEHOLDERS = {"- (none)", "(none)", "(unknown)"}
_PLACEHOLDER_HEAD_WINDOW = 12
_GENERIC_EXACT_TITLES = frozenset(
    {"overview", "problem", "untitled", "review-summary", "report", "task", "todo"})
_GENERIC_TITLE_PREFIX = re.compile(r"^(?:report|task|todo)-")
_DOC_FRAGMENT_MIN_CONTENT_HITS = 2


@dataclass(frozen=True)
class _CensusCorpus:
    """census 本地語料：instruction docs 的正規化 heading／verbatim line 集。"""
    headings: frozenset[str]
    lines: frozenset[str]


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _corpus_from_texts(texts: "Iterable[str]") -> _CensusCorpus:
    headings: set[str] = set()
    lines: set[str] = set()
    for text in texts:
        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            lines.add(_norm_line(s))
            if _HEADING_LINE.match(s):
                headings.add(_norm_line(s.lstrip("#").strip()))
    return _CensusCorpus(frozenset(headings), frozenset(lines))


def _corpus_from_roots(roots: "tuple[str, ...]") -> _CensusCorpus:
    """語料來源檔案集與生產路徑一致（discover_instruction_docs 為純檔案枚舉
    原語）；語料正規化／建構為本地雙寫。falsy roots → 空語料（doc-fragment 關閉）。"""
    if not roots:
        return _corpus_from_texts(())
    texts: list[str] = []
    for doc in discover_instruction_docs(list(roots)):
        try:
            texts.append(doc.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return _corpus_from_texts(texts)


def _census_generic_title(title: object) -> bool:
    if not title:
        return False
    normalized = re.sub(r"[\s_]+", "-", str(title).strip().lower())
    if not normalized:
        return False
    return (normalized in _GENERIC_EXACT_TITLES
            or _GENERIC_TITLE_PREFIX.match(normalized) is not None)


def _census_pool_reason(fm: "Mapping[str, Any]") -> "str | None":
    """pool 排除（frontmatter 層、非刪除級）雙寫版：review-record /
    canary-fixture / generic-title；保留回 None。reason 字串與生產版一字不差。"""
    kind = str(fm.get("artifact_kind") or "").strip().lower()
    if kind == "review":
        return "review-record"
    blob = " ".join(str(fm.get(k, "")) for k in
                    ("atom_title", "title", "session_title")).lower()
    if kind == "task" and ("canary" in blob or "smoke" in blob):
        return "canary-fixture"
    if any(_census_generic_title(fm.get(k)) for k in ("atom_title", "title")):
        return "generic-title"
    return None


def _prose_lines(stripped: str) -> list[str]:
    """散文行：非空白、非 heading、非 list item。"""
    out: list[str] = []
    for line in stripped.splitlines():
        s = line.strip()
        if not s or _HEADING_LINE.match(s) or _LIST_ITEM.match(s):
            continue
        out.append(s)
    return out


def _structural_echo(stripped: str) -> "str | None":
    first = stripped.splitlines()[0].strip() if stripped else ""
    section = _IMPORTER_ECHO_HEADINGS.get(first)
    if section is not None:
        return section
    if _SESSION_META_LINE.match(first):
        return "SessionMetadata"
    if first == "## Summary" and len(_prose_lines(stripped)) <= 1:
        return "Summary"
    return None


def _opens_with_placeholder(stripped: str) -> bool:
    if stripped in _BARE_PLACEHOLDERS:
        return True
    head = stripped[:_PLACEHOLDER_HEAD_WINDOW]
    return any(p in head for p in _PLACEHOLDER_PHRASES)


def _is_hollow(stripped: str) -> bool:
    for line in stripped.splitlines():
        s = line.strip()
        if s and not _HEADING_LINE.match(s):
            return False
    return True


def _is_doc_fragment(stripped: str, corpus: object) -> bool:
    """doc-fragment 判定雙寫版；corpus 為 duck-typed 資料（headings/lines 集）。"""
    headings = getattr(corpus, "headings", frozenset())
    lines = getattr(corpus, "lines", frozenset())
    if not lines:
        return False
    content = [s for s in (ln.strip() for ln in stripped.splitlines()) if s]
    if not content:
        return False
    first = content[0]
    if not _HEADING_LINE.match(first):
        return False
    if _norm_line(first.lstrip("#").strip()) not in headings:
        return False
    hits = 0
    for line in content[1:]:
        if _norm_line(line) in lines:
            hits += 1
            if hits >= _DOC_FRAGMENT_MIN_CONTENT_HITS:
                return True
    return False


def _census_noise_reason(body: str, corpus: object) -> "str | None":
    """noise 排除（body-only、刪除級語義）雙寫版：structural-echo:<Section> /
    placeholder / empty / doc-fragment；非 noise 回 None。判定順序與生產版一致。"""
    stripped = body.strip()
    section = _structural_echo(stripped)
    if section is not None:
        return f"structural-echo:{section}"
    if _opens_with_placeholder(stripped):
        return "placeholder"
    if _is_hollow(stripped):
        return "empty"
    if _is_doc_fragment(stripped, corpus):
        return "doc-fragment"
    return None


def _fate(fpath: Path, corpus_for: "Callable[[str], object]") -> "tuple[str, str | None]":
    """回傳 (fate, slice_id)；fate ∈ {"invalid", "pool:<reason>", "noise:<reason>", "eligible"}。

    分支順序與 reason 字串必須和 build_index 的掃描分支一字不差（見
    search.build_index docstring）；但規則本體是本模組的雙寫實作
    （_census_pool_reason / _census_noise_reason），不呼叫生產 classifier。
    三方對賬靠「兩條獨立迴圈＋兩份獨立規則算出同一分佈」保證——不共用
    同一次掃描，也不共用分類邏輯；共用面只剩 fio.read 低階解析原語。
    """
    try:
        fm, body = fio.read(fpath.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "invalid", None
    if not fm:
        return "invalid", None
    layer = fm.get("memory_layer")
    if layer != "knowledge":
        return f"pool:non-knowledge-layer:{layer or 'none'}", None
    sid = fm.get("slice_id")
    if not sid:
        return "invalid", None
    reason = _census_pool_reason(fm)
    if reason is not None:
        return f"pool:{reason}", str(sid)
    noise_reason = _census_noise_reason(body, corpus_for(str(fm.get("project", ""))))
    if noise_reason is not None:
        return f"noise:{noise_reason}", str(sid)
    return "eligible", str(sid)


def reconcile_index(memory_root: Path, coverage: "Mapping[str, Any]",
                    doc_corpus: "object | None" = None) -> ReconcileResult:
    """三方對賬：filesystem census × coverage 報表 × index DB 反查。

    ``coverage`` 為 build_index() 回傳（或 coverage_path() 落盤）的六鍵報表；
    ``doc_corpus`` 為呼叫方傳入的語料**資料**（需與當初 build_index 的呼叫
    一致；dream 路徑為 None）——census 只 duck-type 讀其 headings / lines
    兩個集合，doc-fragment 比對邏輯仍為本地雙寫（_is_doc_fragment）。
    """
    problems: list[str] = []
    census = filesystem_census(memory_root)

    # 語料「來源」解析（projects.yaml → roots → instruction docs 檔案集）與
    # build_index 對齊——這是輸入面 plumbing；語料正規化與分類規則本體為本地雙寫。
    config = load_projects_config(default_projects_path(memory_root))
    project_roots = {project.slug: project.roots for project in config.projects}
    empty_corpus = _corpus_from_texts(())
    corpus_cache: dict[str, object] = {}

    def corpus_for(project: str) -> object:
        cached = corpus_cache.get(project)
        if cached is not None:
            return cached
        if project in project_roots:
            corpus = _corpus_from_roots(project_roots[project])
        elif doc_corpus is not None and not project_roots:
            corpus = doc_corpus
        else:
            corpus = empty_corpus
        corpus_cache[project] = corpus
        return corpus

    invalid = 0
    pool: dict[str, int] = {}
    noise: dict[str, int] = {}
    eligible_ids: set[str] = set()
    eligible_count = 0
    for entry in census:
        fate, sid = _fate(Path(entry.path), corpus_for)
        if fate == "invalid":
            invalid += 1
        elif fate.startswith("pool:"):
            reason = fate[len("pool:"):]
            pool[reason] = pool.get(reason, 0) + 1
        elif fate.startswith("noise:"):
            reason = fate[len("noise:"):]
            noise[reason] = noise.get(reason, 0) + 1
        else:
            eligible_count += 1
            if sid in eligible_ids:
                problems.append(f"duplicate slice_id on disk: {sid}")
            eligible_ids.add(str(sid))

    # 對賬一：census（磁碟真相）↔ coverage（build_index 的宣稱）
    if len(census) != coverage.get("scanned"):
        problems.append(
            f"census files {len(census)} != coverage scanned {coverage.get('scanned')}")
    if invalid != coverage.get("invalid_frontmatter"):
        problems.append(
            f"census invalid {invalid} != coverage invalid_frontmatter "
            f"{coverage.get('invalid_frontmatter')}")
    if pool != coverage.get("pool_excluded"):
        problems.append(
            f"census pool-excluded {pool} != coverage {coverage.get('pool_excluded')}")
    if noise != coverage.get("noise_excluded"):
        problems.append(
            f"census noise-excluded {noise} != coverage {coverage.get('noise_excluded')}")
    if eligible_count != coverage.get("eligible"):
        problems.append(
            f"census eligible {eligible_count} != coverage eligible {coverage.get('eligible')}")
    # 互斥完備分割：每個檔恰有唯一去向，總和必等於全集
    if invalid + sum(pool.values()) + sum(noise.values()) + eligible_count != len(census):
        problems.append("fate partition does not sum to census total")

    # 對賬二：eligible ID 集（磁碟推導）↔ indexed ID 集（DB 反查）
    try:
        in_db = indexed_ids(memory_root)
    except (SearchIndexError, sqlite3.OperationalError) as exc:
        in_db = set()
        problems.append(f"index unreadable: {exc}")
    else:
        missing = sorted(eligible_ids - in_db)
        extra = sorted(in_db - eligible_ids)
        if missing:
            problems.append(f"eligible but not indexed ({len(missing)}): {missing[:5]}")
        if extra:
            problems.append(f"indexed but not eligible ({len(extra)}): {extra[:5]}")
        if len(in_db) != coverage.get("indexed"):
            problems.append(f"db rows {len(in_db)} != coverage indexed {coverage.get('indexed')}")

    return ReconcileResult(ok=not problems, problems=problems, census_files=len(census),
                           eligible_ids=eligible_ids, indexed_ids=in_db)
