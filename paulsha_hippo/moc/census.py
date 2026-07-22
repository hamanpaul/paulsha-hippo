"""三方對賬（#16）：filesystem census × coverage 報表 × index DB 反查。

build_index() 的 coverage 報表出自它自己的掃描迴圈，單獨看是同源自證。
本模組提供分離實作的獨立驗證面（spec §3.2 驗收「防同源自證」）：

- ``filesystem_census()``：純檔案枚舉（os.walk + 最小 line-based 欄位抽取，
  不走 rglob、不走 yaml），建立磁碟上的檔案／ID 全集。
- 獨立 fate pass（``_fate``）：另一條迴圈對每個 census 檔案指派唯一去向
  （invalid / pool-excluded(reason) / noise-excluded(reason) / eligible）。
  **分類規則刻意雙寫**：pool／noise／tags-錯型 eligibility 規則在本模組內
  獨立重寫（``_census_pool_reason`` / ``_census_noise_reason`` /
  ``_census_tags_invalid``），不 import noise.py 的生產 classifier、也不用
  instruction_corpus 的語料建構 helper——共用生產分類邏輯會把 classifier
  的 bug 同時複製到兩邊，eligible == indexed 照樣通過（同源自證）。
  **身份欄位（slice_id / memory_layer）以 census 自己的 line-based 解析
  （CensusEntry）為基準**，不採 fio.read 的結果——fio.read 與 build_index
  共用，若它誤判磁碟 ID（合法 YAML tag/anchor、或 parser bug），eligible
  端與 DB 端會拿到同一個錯 ID 而 false green；_fate 逐檔交叉比對兩個
  parser 的身份欄位，任何分歧記入 problems 顯性回報。與 indexing 路徑
  共用的只剩：檔案枚舉（os.walk／discover_instruction_docs）、fio.read
  的**非身份**欄位（pool／noise／tags 規則的輸入）、sqlite 查詢、
  projects.yaml 設定讀取。
- ``audit_indexed_ids()``：從建好的 retrieval.db 反查**搜尋面真相**——實際
  搜尋走 slices_fts INNER JOIN slice_meta（search.search()），故兩表都驗：
  FTS integrity-check + 兩表 slice_id multiset 一對一（缺行／幽靈行／重複行
  顯性回報）；只查 slice_meta 會對 FTS-only corruption false green。

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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..atomizer.publication import committed_publication_ids
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
    # DB 反查的「可搜尋」ID 集（slice_meta ∩ slices_fts；見 audit_indexed_ids）
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


@dataclass
class DbAudit:
    """retrieval.db 搜尋面反查結果。

    ``searchable_ids`` 為 slice_meta ∩ slices_fts——實際可被 search() 回傳
    的 slice_id 全集；``problems`` 回報兩表對賬與 FTS integrity 問題。
    """
    searchable_ids: set[str] = field(default_factory=set)
    meta_rows: int = 0
    problems: list[str] = field(default_factory=list)


def audit_indexed_ids(memory_root: Path) -> DbAudit:
    """DB 反查搜尋面真相：驗證真正提供搜尋結果的兩張表。

    實際搜尋必須 slices_fts INNER JOIN slice_meta（search.search()）——
    只反查 slice_meta 時，FTS row 遺失（搜尋漏資料）或重複（搜尋重複回傳）
    而 metadata 完整的 corruption 會 false green。三層驗證：

    1. FTS integrity-check（FTS5 特殊命令；倒排索引 ↔ content shadow table
       自檢，只檢查不改資料，壞掉 raise SQLITE_CORRUPT_VTAB）。
    2. 兩表 slice_id multiset 一對一：缺行／幽靈行／重複行顯性回報。
    3. ``searchable_ids`` 取兩表交集，供 reconcile 對賬 eligible IDs。
    """
    path = index_path(memory_root)
    if not path.exists():
        raise SearchIndexError("search index not built; run the dream/moc pass first")
    problems: list[str] = []
    conn = sqlite3.connect(path)
    try:
        meta_ids = [row[0] for row in conn.execute("SELECT slice_id FROM slice_meta")]
        try:
            conn.execute("INSERT INTO slices_fts(slices_fts) VALUES('integrity-check')")
        except sqlite3.DatabaseError as exc:
            problems.append(f"slices_fts integrity-check failed: {exc}")
        try:
            fts_ids = [row[0] for row in conn.execute("SELECT slice_id FROM slices_fts")]
        except sqlite3.DatabaseError as exc:
            problems.append(f"slices_fts unreadable: {exc}")
            fts_ids = []
    finally:
        conn.close()

    meta_counts = Counter(meta_ids)
    fts_counts = Counter(fts_ids)
    dup_meta = sorted(sid for sid, count in meta_counts.items() if count > 1)
    if dup_meta:  # schema PK 理論上擋掉；防外來／壞 DB
        problems.append(f"duplicate slice_meta rows ({len(dup_meta)}): {dup_meta[:5]}")
    dup_fts = sorted(sid for sid, count in fts_counts.items() if count > 1)
    if dup_fts:  # FTS5 無唯一約束：重複行會讓搜尋重複回傳同一 slice
        problems.append(f"duplicate slices_fts rows ({len(dup_fts)}): {dup_fts[:5]}")
    meta_only = sorted(set(meta_counts) - set(fts_counts))
    if meta_only:  # 有 metadata、進不了 FTS join：搜尋漏資料
        problems.append(
            f"in slice_meta but missing from slices_fts ({len(meta_only)}): {meta_only[:5]}")
    fts_only = sorted(set(fts_counts) - set(meta_counts))
    if fts_only:  # 幽靈 FTS 行：inner join 靜默丟棄
        problems.append(
            f"in slices_fts but missing from slice_meta ({len(fts_only)}): {fts_only[:5]}")
    return DbAudit(searchable_ids=set(meta_counts) & set(fts_counts),
                   meta_rows=len(meta_ids), problems=problems)


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


def _census_tags_invalid(tags: object) -> bool:
    """tags 錯型判定雙寫版（與 search._tags_fts_text 的嚴格驗證對齊）：
    缺欄／null 合法；否則必須是全字串元素的 list，錯型（如 ``tags: [1]``）
    歸 invalid_frontmatter。"""
    if tags is None:
        return False
    return not (isinstance(tags, list)
                and all(isinstance(tag, str) for tag in tags))


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


def _fate(entry: CensusEntry,
          corpus_for: "Callable[[str], object]",
          committed_publications: "set[str] | None" = None,
          ) -> "tuple[str, str | None, list[str]]":
    """回傳 (fate, slice_id, identity_problems)；fate ∈ {"invalid",
    "pool:<reason>", "noise:<reason>", "eligible"}。

    **身份基準**：slice_id / memory_layer 取 census 自己 line-based 獨立解析
    的 ``entry`` 欄位，不採 fio.read 的結果——fio.read 與 build_index 共用，
    若它誤判磁碟 ID（合法 YAML tag 如 ``!!str sl-x``、anchor、或 parser
    bug），eligible 端與 DB 端會拿到同一個錯 ID，reconcile 照樣全綠
    （false green，破壞 spec §3.2 防同源自證）。fio.read 在此僅供：
    (a) 身份欄位逐檔交叉比對，任何分歧記入 identity_problems 顯性回報；
    (b) 非身份欄位（pool／noise／tags 規則的輸入），不參與 ID 對賬。

    分支順序與 reason 字串必須和 build_index 的掃描分支一字不差（見
    search.build_index docstring）；但規則本體是本模組的雙寫實作
    （_census_pool_reason / _census_noise_reason / _census_tags_invalid），
    不呼叫生產 classifier。唯一不在本函式的分支：pool[duplicate-slice-id-
    on-disk]（先到先贏 dedup）是跨檔案狀態，逐檔的 _fate 放不下，鏡像規則
    落在 reconcile_index 迴圈。三方對賬靠「兩條獨立迴圈＋兩份獨立規則算出
    同一分佈」保證——不共用同一次掃描，也不共用分類邏輯。
    """
    name = Path(entry.path).name
    try:
        fm, body = fio.read(Path(entry.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "invalid", None, []
    if not fm:
        problems: list[str] = []
        if entry.slice_id is not None or entry.memory_layer is not None:
            problems.append(
                f"identity divergence in {name}: census parsed slice_id="
                f"{entry.slice_id!r} memory_layer={entry.memory_layer!r} "
                "but production frontmatter parser found none")
        return "invalid", None, problems
    problems = []
    for field_name, census_value in (("slice_id", entry.slice_id),
                                     ("memory_layer", entry.memory_layer)):
        production = fm.get(field_name)
        production_norm = None if production is None else str(production)
        if census_value != production_norm:
            problems.append(
                f"identity divergence in {name}: census {field_name}="
                f"{census_value!r} != production parser {production_norm!r}")
    if entry.memory_layer != "knowledge":
        return (f"pool:non-knowledge-layer:{entry.memory_layer or 'none'}",
                None, problems)
    sid = entry.slice_id
    if not sid:
        return "invalid", None, problems
    try:
        if _census_tags_invalid(fm.get("tags")):
            return "invalid", None, problems
        reason = _census_pool_reason(fm)
        if reason is not None:
            return f"pool:{reason}", sid, problems
        if (fm.get("publication_id") is not None
                and str(fm.get("publication_id")) not in (committed_publications or set())):
            return "pool:publication-pending", sid, problems
        noise_reason = _census_noise_reason(body, corpus_for(str(fm.get("project", ""))))
    except Exception:
        # 對賬工具自身不得被單一毒 slice 炸掉：鏡像 build_index 的 per-slice
        # 邊界收斂成 invalid。若只有單邊 crash，分佈比對會顯性回報不一致。
        return "invalid", None, problems
    if noise_reason is not None:
        return f"noise:{noise_reason}", sid, problems
    return "eligible", sid, problems


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
    committed_publications = committed_publication_ids(memory_root)
    for entry in census:
        fate, sid, identity_problems = _fate(entry, corpus_for, committed_publications)
        problems.extend(identity_problems)
        if fate == "invalid":
            invalid += 1
        elif fate.startswith("pool:"):
            reason = fate[len("pool:"):]
            pool[reason] = pool.get(reason, 0) + 1
        elif fate.startswith("noise:"):
            reason = fate[len("noise:"):]
            noise[reason] = noise.get(reason, 0) + 1
        elif sid in eligible_ids:
            # build_index 先到先贏 dedup 的鏡像規則（跨檔案狀態放不進逐檔的
            # _fate）：同 slice_id 的後到 eligible 檔歸 pool[duplicate-slice-
            # id-on-disk]，分佈與 coverage 對齊；磁碟真相異常本身仍顯性回報
            # （naming dedup fail-soft 後可能殘留，operator 應被提醒）。
            pool["duplicate-slice-id-on-disk"] = (
                pool.get("duplicate-slice-id-on-disk", 0) + 1)
            problems.append(f"duplicate slice_id on disk: {sid}")
        else:
            eligible_count += 1
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

    # 對賬二：eligible ID 集（磁碟推導）↔ 搜尋面 ID 集（DB 兩表反查：
    # slice_meta ↔ slices_fts multiset 一對一 + FTS integrity-check）
    try:
        audit = audit_indexed_ids(memory_root)
    except (SearchIndexError, sqlite3.DatabaseError) as exc:
        in_db: set[str] = set()
        problems.append(f"index unreadable: {exc}")
    else:
        in_db = audit.searchable_ids
        problems.extend(audit.problems)
        missing = sorted(eligible_ids - in_db)
        extra = sorted(in_db - eligible_ids)
        if missing:
            problems.append(f"eligible but not indexed ({len(missing)}): {missing[:5]}")
        if extra:
            problems.append(f"indexed but not eligible ({len(extra)}): {extra[:5]}")
        if audit.meta_rows != coverage.get("indexed"):
            problems.append(
                f"db rows {audit.meta_rows} != coverage indexed {coverage.get('indexed')}")

    return ReconcileResult(ok=not problems, problems=problems, census_files=len(census),
                           eligible_ids=eligible_ids, indexed_ids=in_db)
