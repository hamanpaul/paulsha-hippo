"""#148 H2 離線 benchmark 的凍結 as-of BM25 基準線（#164）。

H2 的 A／B／C 三組共用同一份「BM25 top-12 候選」。本模組把它凍結成可重現的資料：

- **as-of 檢索**：task 文字（issue 標題＋內文摘錄）經 ``retrieval.to_fts_query()`` 淨化後，
  在 task 所屬 project 的 FTS5 索引中檢索；只保留 ``captured_at`` 早於 as-of（issue 建立
  時間）的 slice，時間解析不出來的排除；標題或內文提到 task 自身 issue 號碼的也排除。
- **純 bm25 排序**：正式 ``search()`` 的 link_weight、已讀加權與 active 旗標都是「現在」
  的狀態，拿來排 as-of 的題目會帶進未來資訊，因此基準線只用 FTS5 ``bm25()``（越負越相關），
  同分以 slice_id 決定；as-of 之前的 slice 不論現在是否 decayed 都納入。A 組＝top-3。
- **送出前掃描**（C 組會把候選送外部 processor）：候選只取標題＋內文前 800 字元（不含
  frontmatter），命中本機絕對路徑、email（git remote 位址除外）、private IP、疑似 secret，或私有字詞清單者整則
  標為不可送出；task 文字本身也要掃。私有字詞清單由呼叫端從私有檔案提供，不進 repo。
- **凍結**：索引先複製成快照並記錄 sha256，檢索只讀快照；輸出含整份 digest（不含
  ``frozen_at``）。同一份快照、task 檔與私有字詞清單重跑，digest 相同。

輸出含記憶內文，只能放在私有位置，不得提交進 repo（比照 #158 真實樣本的規則）。
本模組唯讀、不呼叫任何 LLM 或外部服務。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from paulsha_hippo.moc.search import index_path
from paulsha_hippo.retrieval import to_fts_query

__all__ = [
    "A_K",
    "BODY_VIEW_CHARS",
    "FETCH_K",
    "FROZEN_FORMAT",
    "TASKS_FORMAT",
    "H2Task",
    "H2TaskError",
    "deny_scan",
    "freeze",
    "load_deny_terms",
    "load_tasks",
    "retrieve_as_of",
]

TASKS_FORMAT = "hippo-h2-tasks"
FROZEN_FORMAT = "hippo-h2-frozen-candidates"
FORMAT_VERSION = 1
FETCH_K = 12
A_K = 3
BODY_VIEW_CHARS = 800
SPLITS = ("dev", "hidden-pool")

#: 通用送出規則（不含任何私有字詞；私有清單由 load_deny_terms 從私有檔案載入）。
_BUILTIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("abs-path-posix", re.compile(r"(?<![\w.])/(?:home|Users|root|mnt/[a-z])/[^\s/]+", re.IGNORECASE)),
    ("abs-path-windows", re.compile(r"\b[A-Za-z]:\\\\?[^\s\\]+")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("private-ip", re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("secret", re.compile(
        r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
)
_ISSUE_REPO = re.compile(r"^github\.com/[\w.-]+/[\w.-]+$")
_GIT_REMOTE = re.compile(r"\bgit@(?:github|gitlab)\.com\b", re.IGNORECASE)


class H2TaskError(ValueError):
    """task 檔或參數不合法。"""


@dataclass(frozen=True)
class H2Task:
    task_id: str
    repo: str
    issue: int
    title: str
    body: str
    as_of: dt.datetime
    split: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


def parse_time(value: Any) -> dt.datetime | None:
    """解析 ISO8601 或 ``captured_at`` 的「空白分隔」格式；無時區或解析失敗回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def load_tasks(path: Path) -> list[H2Task]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise H2TaskError(f"無法讀取 task 檔：{exc}") from exc
    if not isinstance(data, dict) or data.get("format") != TASKS_FORMAT or data.get("version") != FORMAT_VERSION:
        raise H2TaskError(f"task 檔頂層需為 format={TASKS_FORMAT!r}、version={FORMAT_VERSION}")
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise H2TaskError("tasks 必須是非空陣列")
    tasks: list[H2Task] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_tasks):
        where = f"tasks[{index}]"
        if not isinstance(raw, dict):
            raise H2TaskError(f"{where} 必須是 object")
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen:
            raise H2TaskError(f"{where}.task_id 必須是唯一的非空字串")
        seen.add(task_id)
        repo = raw.get("repo")
        if not isinstance(repo, str) or not _ISSUE_REPO.match(repo):
            raise H2TaskError(f"{where}.repo 必須形如 github.com/<owner>/<repo>")
        issue = raw.get("issue")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
            raise H2TaskError(f"{where}.issue 必須是正整數")
        title, body = raw.get("title"), raw.get("body", "")
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise H2TaskError(f"{where} 需要非空 title 與字串 body")
        as_of = parse_time(raw.get("as_of"))
        if as_of is None:
            raise H2TaskError(f"{where}.as_of 必須是含時區的 ISO8601 時間")
        split = raw.get("split")
        if split not in SPLITS:
            raise H2TaskError(f"{where}.split 必須是 {SPLITS}")
        tasks.append(H2Task(task_id, repo, issue, title.strip(), body.strip(), as_of, split))
    return tasks


def load_deny_terms(path: Path | None) -> tuple[str, ...]:
    """私有字詞清單：一行一個，不分大小寫的子字串比對；空行與 # 開頭略過。"""
    if path is None:
        return ()
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line.lower())
    return tuple(sorted(set(terms)))


def deny_scan(text: str, deny_terms: Iterable[str] = ()) -> list[str]:
    """回傳命中的規則名稱（排序、去重）；空 list 代表可送出。私有字詞只回報 ``private-term``。"""
    text = _GIT_REMOTE.sub("", text)  # git@github.com 這類 remote 位址不是個人 email
    hits = {name for name, pattern in _BUILTIN_RULES if pattern.search(text)}
    lowered = text.lower()
    if any(term in lowered for term in deny_terms):
        hits.add("private-term")
    return sorted(hits)


def _issue_ref_pattern(issue: int) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w/])#{issue}(?!\d)")


def _body_view(body: str) -> str:
    return body.strip()[:BODY_VIEW_CHARS]


@dataclass
class RetrievalResult:
    candidates: list[dict] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    matched: int = 0


def retrieve_as_of(conn: sqlite3.Connection, task: H2Task, *, deny_terms: Iterable[str] = (),
                   fetch_k: int = FETCH_K) -> RetrievalResult:
    """在索引快照上做 as-of 檢索；回傳 top-``fetch_k`` 候選與排除統計。"""
    result = RetrievalResult()
    match_query = to_fts_query(task.text)
    if not match_query:
        return result
    rows = conn.execute(
        "SELECT f.slice_id, f.title, f.body, bm25(slices_fts) AS bm, m.captured_at "
        "FROM slices_fts f JOIN slice_meta m ON m.slice_id = f.slice_id "
        "WHERE slices_fts MATCH ? AND m.project = ?",
        (match_query, task.repo),
    ).fetchall()
    result.matched = len(rows)
    ref = _issue_ref_pattern(task.issue)
    eligible = []
    for slice_id, title, body, bm, captured_at in rows:
        captured = parse_time(captured_at)
        if captured is None:
            result.excluded["time-untrusted"] = result.excluded.get("time-untrusted", 0) + 1
            continue
        if captured >= task.as_of:
            result.excluded["after-as-of"] = result.excluded.get("after-as-of", 0) + 1
            continue
        if ref.search(title or "") or ref.search(body or ""):
            result.excluded["mentions-task-issue"] = result.excluded.get("mentions-task-issue", 0) + 1
            continue
        eligible.append((float(bm), str(slice_id), title or "", body or "", captured))
    eligible.sort(key=lambda row: (row[0], row[1]))
    deny_terms = tuple(deny_terms)
    for rank, (bm, slice_id, title, body, captured) in enumerate(eligible[:fetch_k], start=1):
        view = _body_view(body)
        hits = deny_scan(f"{title}\n{view}", deny_terms)
        result.candidates.append({
            "rank": rank,
            "slice_id": slice_id,
            "bm25": round(bm, 6),
            "captured_at": captured.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "title": title,
            "body_view": view,
            "egress": "eligible" if not hits else "excluded",
            "egress_reasons": hits,
        })
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def freeze(memory_root: Path, tasks: list[H2Task], out_dir: Path, *, deny_terms: Iterable[str] = (),
           allowlist: Iterable[str], frozen_at: dt.datetime, snapshot: Path | None = None) -> dict:
    """建立（或沿用）索引快照，對每個 task 做 as-of 檢索，寫出凍結候選集並回傳摘要。

    ``snapshot`` 省略時，把 ``memory_root`` 的現行索引複製到 ``out_dir/index-snapshot.db``；
    給定時直接沿用（重跑驗證用）。
    """
    allow = tuple(sorted(set(allowlist)))
    if not allow:
        raise H2TaskError("public allowlist 不可為空")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if snapshot is None:
        source = index_path(Path(memory_root))
        if not source.is_file():
            raise H2TaskError(f"找不到檢索索引：{source}")
        snapshot = out_dir / "index-snapshot.db"
        shutil.copyfile(source, snapshot)
    snapshot = Path(snapshot)
    deny_terms = tuple(deny_terms)
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    records = []
    try:
        for task in tasks:
            task_hits = deny_scan(task.text, deny_terms)
            if task.repo not in allow:
                task_egress = "excluded"
                task_hits = sorted({*task_hits, "repo-not-allowlisted"})
            else:
                task_egress = "eligible" if not task_hits else "excluded"
            retrieved = retrieve_as_of(conn, task, deny_terms=deny_terms)
            records.append({
                "task_id": task.task_id,
                "split": task.split,
                "repo": task.repo,
                "issue": task.issue,
                "as_of": task.as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": task.title,
                "body": task.body,
                "fts_query": to_fts_query(task.text),
                "task_egress": task_egress,
                "task_egress_reasons": task_hits,
                "matched": retrieved.matched,
                "excluded_before_rank": dict(sorted(retrieved.excluded.items())),
                "candidates": retrieved.candidates,
                "arm_a": [c["slice_id"] for c in retrieved.candidates[:A_K]],
            })
    finally:
        conn.close()
    payload = {
        "format": FROZEN_FORMAT,
        "version": FORMAT_VERSION,
        "params": {
            "fetch_k": FETCH_K,
            "a_k": A_K,
            "body_view_chars": BODY_VIEW_CHARS,
            "ranking": "pure-fts5-bm25-asc-then-slice_id",
            "as_of_field": "captured_at",
            "public_allowlist": list(allow),
            "deny_terms_sha256": hashlib.sha256(_canonical(list(deny_terms)).encode("utf-8")).hexdigest(),
        },
        "index_snapshot_sha256": _sha256_file(snapshot),
        "tasks": records,
    }
    payload["digest"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    payload["frozen_at"] = frozen_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (out_dir / "frozen-candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summarize(payload)


def summarize(payload: dict) -> dict:
    """覆蓋統計：每題候選數、可送出比例、各排除原因的數量。"""
    tasks = payload["tasks"]
    counts = [len(t["candidates"]) for t in tasks]
    candidates = [c for t in tasks for c in t["candidates"]]
    excluded: dict[str, int] = {}
    for task in tasks:
        for reason, count in task["excluded_before_rank"].items():
            excluded[reason] = excluded.get(reason, 0) + count
    egress_reasons: dict[str, int] = {}
    for candidate in candidates:
        for reason in candidate["egress_reasons"]:
            egress_reasons[reason] = egress_reasons.get(reason, 0) + 1
    return {
        "digest": payload["digest"],
        "index_snapshot_sha256": payload["index_snapshot_sha256"],
        "tasks": len(tasks),
        "tasks_by_split": {s: sum(1 for t in tasks if t["split"] == s) for s in SPLITS},
        "tasks_with_no_candidate": sum(1 for c in counts if c == 0),
        "tasks_with_full_top12": sum(1 for c in counts if c == FETCH_K),
        "candidates_total": len(candidates),
        "candidates_egress_eligible": sum(1 for c in candidates if c["egress"] == "eligible"),
        "tasks_egress_eligible": sum(1 for t in tasks if t["task_egress"] == "eligible"),
        "excluded_before_rank": dict(sorted(excluded.items())),
        "egress_reasons": dict(sorted(egress_reasons.items())),
    }
