"""#164：H2 離線 benchmark 的凍結 as-of BM25 基準線。只用合成索引與合成 task，不含任何真實記憶。"""

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from paulsha_hippo import cli
from paulsha_hippo import h2_offline as H
from paulsha_hippo.moc.search import index_path

REPO = "github.com/example/widget"
OTHER = "github.com/example/gadget"
AS_OF = "2026-09-01T00:00:00Z"


def _build_index(memory_root: Path, rows: list[tuple]) -> Path:
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE VIRTUAL TABLE slices_fts USING fts5("
                 "slice_id UNINDEXED, project, title, tags, body, tokenize='unicode61')")
    conn.execute("CREATE TABLE slice_meta (slice_id TEXT PRIMARY KEY, project TEXT, captured_at TEXT, "
                 "active INTEGER, link_weight INTEGER, path TEXT, read_count INTEGER NOT NULL DEFAULT 0, "
                 "last_read_at TEXT)")
    for slice_id, project, title, body, captured_at in rows:
        conn.execute("INSERT INTO slices_fts VALUES (?,?,?,?,?)", (slice_id, project, title, "", body))
        # active=0、link_weight 很大、read_count 很大：基準線必須忽略這些「現在」的訊號。
        conn.execute("INSERT INTO slice_meta VALUES (?,?,?,?,?,?,?,?)",
                     (slice_id, project, captured_at, 0, 99, f"knowledge/{slice_id}.md", 50, None))
    conn.commit()
    conn.close()
    return path


BEFORE = "2026-08-15 10:00:00.000000+00:00"
AFTER = "2026-09-02 10:00:00.000000+00:00"


def _rows() -> list[tuple]:
    rows = [
        ("s-best", REPO, "cursor tenant binding", "cursor tenant binding cursor tenant", BEFORE),
        ("s-after", REPO, "cursor tenant later", "cursor tenant binding decided later", AFTER),
        ("s-badtime", REPO, "cursor tenant", "cursor tenant untrusted time", "not-a-time"),
        ("s-naive", REPO, "cursor tenant", "cursor tenant naive time", "2026-08-15 10:00:00"),
        ("s-mentions", REPO, "cursor tenant", "follow-up for #7 cursor tenant", BEFORE),
        ("s-path", REPO, "cursor note", "cursor tenant see /home/alice/work/notes.md", BEFORE),
        ("s-private", REPO, "cursor note", "cursor tenant from ACME-Internal board", BEFORE),
        ("s-email", REPO, "cursor note", "cursor tenant ask bob@example.org", BEFORE),
        ("s-git", REPO, "cursor remote", "cursor tenant via git@github.com:example/widget.git", BEFORE),
        ("s-other", OTHER, "cursor tenant", "cursor tenant binding", BEFORE),
    ]
    rows += [(f"s-fill-{i:02d}", REPO, f"cursor filler {i}", "cursor " + "pad " * (i + 1), BEFORE) for i in range(14)]
    return rows


def _tasks(tmp_path: Path, tasks: list[dict] | None = None) -> Path:
    tasks = tasks or [
        {"task_id": "t-dev", "repo": REPO, "issue": 7, "title": "Bind cursor to tenant",
         "body": "cursor tenant binding", "as_of": AS_OF, "split": "dev"},
        {"task_id": "t-mail", "repo": REPO, "issue": 8, "title": "Cursor tenant",
         "body": "ping carol@example.org about cursor", "as_of": AS_OF, "split": "hidden-pool"},
        {"task_id": "t-other", "repo": OTHER, "issue": 3, "title": "Cursor tenant",
         "body": "cursor tenant binding", "as_of": AS_OF, "split": "hidden-pool"},
    ]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"format": H.TASKS_FORMAT, "version": 1, "tasks": tasks}), encoding="utf-8")
    return path


FROZEN_AT = dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)


def _freeze(tmp_path: Path, **kwargs) -> tuple[dict, dict]:
    root = tmp_path / "memory"
    if not index_path(root).exists():
        _build_index(root, _rows())
    out = kwargs.pop("out", tmp_path / "out")
    summary = H.freeze(root, H.load_tasks(_tasks(tmp_path)), out, deny_terms=("acme-internal",),
                       allowlist=[REPO], frozen_at=FROZEN_AT, **kwargs)
    payload = json.loads((out / "frozen-candidates.json").read_text(encoding="utf-8"))
    return summary, payload


def _task(payload: dict, task_id: str) -> dict:
    return next(t for t in payload["tasks"] if t["task_id"] == task_id)


def test_as_of_filter_and_leak_guards(tmp_path):
    _summary, payload = _freeze(tmp_path)
    task = _task(payload, "t-dev")
    ids = [c["slice_id"] for c in task["candidates"]]
    assert "s-after" not in ids and "s-badtime" not in ids and "s-naive" not in ids
    assert "s-mentions" not in ids and "s-other" not in ids
    assert task["excluded_before_rank"] == {"after-as-of": 1, "mentions-task-issue": 1, "time-untrusted": 2}
    # 其他 task 的 issue 號碼不同（#8），slice 裡的 "#7" 不構成排除
    assert "mentions-task-issue" not in _task(payload, "t-mail")["excluded_before_rank"]


def test_pure_bm25_ranking_ignores_current_state_signals_and_cuts_top12(tmp_path):
    _summary, payload = _freeze(tmp_path)
    candidates = _task(payload, "t-dev")["candidates"]
    assert len(candidates) == H.FETCH_K
    scores = [(c["bm25"], c["slice_id"]) for c in candidates]
    assert scores == sorted(scores)
    assert candidates[0]["slice_id"] == "s-best"
    assert [c["rank"] for c in candidates] == list(range(1, 13))
    assert _task(payload, "t-dev")["arm_a"] == [c["slice_id"] for c in candidates[:3]]


def test_egress_scan_marks_whole_candidates_and_tasks(tmp_path):
    _summary, payload = _freeze(tmp_path)
    by_id = {c["slice_id"]: c for c in _task(payload, "t-dev")["candidates"]}
    assert by_id["s-path"]["egress_reasons"] == ["abs-path-posix"]
    assert by_id["s-private"]["egress_reasons"] == ["private-term"]
    assert by_id["s-email"]["egress_reasons"] == ["email"]
    assert by_id["s-git"]["egress"] == "eligible"
    assert by_id["s-best"]["egress"] == "eligible"
    assert _task(payload, "t-dev")["task_egress"] == "eligible"
    assert _task(payload, "t-mail")["task_egress_reasons"] == ["email"]
    assert _task(payload, "t-other")["task_egress_reasons"] == ["repo-not-allowlisted"]
    # 私有字詞原文不寫進輸出，只留 digest
    assert "acme-internal" not in json.dumps(payload["params"]).lower()


def test_body_view_is_capped(tmp_path):
    root = tmp_path / "memory"
    _build_index(root, [("s-long", REPO, "cursor", "cursor tenant " + "x" * 5000, BEFORE)])
    _summary, payload = _freeze(tmp_path)
    assert len(_task(payload, "t-dev")["candidates"][0]["body_view"]) == H.BODY_VIEW_CHARS


def test_digest_is_reproducible_from_snapshot(tmp_path):
    summary, payload = _freeze(tmp_path)
    snapshot = tmp_path / "out" / "index-snapshot.db"
    again, payload2 = _freeze(tmp_path, out=tmp_path / "again", snapshot=snapshot)
    assert summary["digest"] == again["digest"] == payload["digest"] == payload2["digest"]
    assert payload["index_snapshot_sha256"] == payload2["index_snapshot_sha256"]
    assert summary["tasks_by_split"] == {"dev": 1, "hidden-pool": 2}
    assert summary["tasks_egress_eligible"] == 1


@pytest.mark.parametrize("mutate", [
    lambda t: t.update(repo="example/widget"),
    lambda t: t.update(issue=0),
    lambda t: t.update(as_of="2026-09-01"),
    lambda t: t.update(split="hidden"),
    lambda t: t.update(title=" "),
])
def test_load_tasks_rejects_invalid(tmp_path, mutate):
    task = {"task_id": "t", "repo": REPO, "issue": 1, "title": "x", "body": "", "as_of": AS_OF, "split": "dev"}
    mutate(task)
    with pytest.raises(H.H2TaskError):
        H.load_tasks(_tasks(tmp_path, [task]))


def test_deny_scan_rules():
    assert H.deny_scan("clean text") == []
    assert H.deny_scan("C:\\Users\\bob\\x") == ["abs-path-windows"]
    assert H.deny_scan("host 192.168.1.20 and 10.0.0.1") == ["private-ip"]
    assert H.deny_scan("token ghp_" + "a" * 30) == ["secret"]
    assert H.deny_scan("public 8.8.8.8 and example.com/path") == []
    assert H.deny_scan("see Board-X", ("board-x",)) == ["private-term"]


def test_cli_freeze(tmp_path, capsys):
    root = tmp_path / "memory"
    _build_index(root, _rows())
    deny = tmp_path / "deny.txt"
    deny.write_text("# private\nACME-Internal\n", encoding="utf-8")
    code = cli.main(["h2", "freeze", "--memory-root", str(root), "--tasks", str(_tasks(tmp_path)),
                     "--out-dir", str(tmp_path / "cli-out"), "--allow-repo", REPO, "--deny-terms", str(deny)])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tasks"] == 3 and summary["candidates_total"] > 0
    assert (tmp_path / "cli-out" / "index-snapshot.db").is_file()
    assert cli.main(["h2", "freeze", "--memory-root", str(tmp_path / "missing"), "--tasks",
                     str(_tasks(tmp_path)), "--out-dir", str(tmp_path / "x"), "--allow-repo", REPO]) == 1
