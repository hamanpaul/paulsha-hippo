import json
import os
import subprocess
from pathlib import Path
from paulsha_hippo import provenance_backfill as pb
from paulsha_hippo.moc import frontmatter_io as fio

_FM = ("---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: proj\ntitle: T\ncaptured_at: \"2026-08-17T03:32:41Z\"\n"
       "supersedes: []\nprovenance:\n  repo: r\n  commit: {commit}\n  path: {archive}\n"
       "distiller:\n  profile_id: claude\n---\n")


def _note(mr: Path, sid: str, commit: str, archive: str, body: str) -> Path:
    p = mr / "knowledge" / "proj" / f"t--{sid}.md"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_FM.format(sid=sid, commit=commit, archive=archive) + body, encoding="utf-8")
    return p


# captured_at 故意不加引號：yaml.safe_load（frontmatter_io.read() 用的解析器）會把
# 這種寫法隱式解析成 datetime.datetime，而不是 str（review round 2 #1）。
_FM_UNQUOTED_CAPTURED_AT = (
    "---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: proj\ntitle: T\n"
    "captured_at: {captured_at}\nsupersedes: []\nprovenance:\n  repo: r\n  commit: {commit}\n"
    "  path: {archive}\ndistiller:\n  profile_id: claude\n---\n"
)


def _note_unquoted_captured_at(mr: Path, sid: str, commit: str, archive: str, captured_at: str, body: str) -> Path:
    p = mr / "knowledge" / "proj" / f"t--{sid}.md"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _FM_UNQUOTED_CAPTURED_AT.format(sid=sid, commit=commit, archive=archive, captured_at=captured_at) + body,
        encoding="utf-8",
    )
    return p


def _archive(mr: Path, name: str, payload: dict) -> str:
    a = mr / "archive" / "queue" / "2026-08" / name; a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(json.dumps(payload)); return str(a)


def _fakes(head="c" * 40):
    return dict(toplevel=lambda cwd: "/repo" if cwd == "/repo" else None,
                rev_before=lambda top, ts: head if ts else None)


def test_dry_run_reports_and_writes_nothing(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p1 = _note(tmp_path, "sl-1", "_unknown", a, "README-ARC.md:108 已不符\n")
    p2 = _note(tmp_path, "sl-2", "_unknown", str(tmp_path / "missing.json"), "無引用\n")
    before = (p1.read_bytes(), p2.read_bytes())
    summary, warnings = pb.run(tmp_path, apply=False, **_fakes())
    assert summary["commit_candidates"] == 1 and summary["commit_reasons"] == {"no-archive": 1}
    assert summary["cites_candidates"] == 1 and summary["updated"] == 0
    assert (p1.read_bytes(), p2.read_bytes()) == before and warnings == []


def test_apply_is_idempotent_and_marks_source(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p = _note(tmp_path, "sl-1", "_unknown", a, "見 README-ARC.md:108。\n")
    s1, _ = pb.run(tmp_path, apply=True, **_fakes())
    assert s1["updated"] == 1
    fm, body = fio.read(p.read_text(encoding="utf-8"))
    assert fm["provenance"]["commit"] == "c" * 40 and fm["provenance"]["commit_source"] == "backfill-approx"
    assert fm["cites"] == [{"path": "README-ARC.md", "line": 108}] and body == "見 README-ARC.md:108。\n"
    s2, _ = pb.run(tmp_path, apply=False, **_fakes())
    assert s2["commit_candidates"] == 0 and s2["cites_candidates"] == 0
    first = p.read_bytes()
    pb.run(tmp_path, apply=True, **_fakes())
    assert p.read_bytes() == first


def test_known_commit_is_never_overwritten(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p = _note(tmp_path, "sl-1", "2a655c3", a, "x\n")
    summary, _ = pb.run(tmp_path, apply=True, **_fakes())
    assert summary["commit_candidates"] == 0
    assert fio.read(p.read_text())[0]["provenance"]["commit"] == "2a655c3"


def test_provenance_path_escaping_archive_queue_is_reported_and_left_alone(tmp_path):
    # provenance.path 指向 tmp_path 底下、但不在 <memory_root>/archive/queue 之內的
    # 可讀 JSON（review round 1 #1）：不得產生 commit candidate，只計 path-escape，
    # note 完全不動——即使 apply=True。
    outside = tmp_path / "outside" / "s1.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps({"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"}))
    p = _note(tmp_path, "sl-1", "_unknown", str(outside), "無引用\n")
    before = p.read_bytes()
    summary, warnings = pb.run(tmp_path, apply=True, **_fakes())
    assert summary["commit_candidates"] == 0
    assert summary["commit_reasons"] == {"path-escape": 1}
    assert p.read_bytes() == before
    assert warnings == []


def test_cites_rule_rewrites_when_differs_not_only_when_absent(tmp_path):
    # 計畫決定（brief line 14）：cites 規則是「differs or missing」，不是
    # controller 誤述的「absent only」。既有 cites 與 extract_cites(body) 不同時
    # 仍要被改寫成新抽取結果；已相符者原樣不動（byte-identical）。
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p_stale = _note(tmp_path, "sl-1", "2a655c3", a, "見 README-ARC.md:108。\n")
    fio.update(p_stale, {"cites": [{"path": "OLD.md", "line": 1}]})
    p_fresh = _note(tmp_path, "sl-2", "2a655c3", a, "見 README-ARC.md:108。\n")
    fio.update(p_fresh, {"cites": [{"path": "README-ARC.md", "line": 108}]})
    before_fresh = p_fresh.read_bytes()

    summary, warnings = pb.run(tmp_path, apply=True, **_fakes())

    assert summary["commit_candidates"] == 0
    assert summary["cites_candidates"] == 1 and summary["updated"] == 1
    fm_stale, body_stale = fio.read(p_stale.read_text(encoding="utf-8"))
    assert fm_stale["cites"] == [{"path": "README-ARC.md", "line": 108}]
    assert body_stale == "見 README-ARC.md:108。\n"
    assert p_fresh.read_bytes() == before_fresh
    assert warnings == []


def _init_repo_with_commit(repo: Path, when: str) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "c1"],
                    check=True, env=env)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()


def test_missing_ended_at_and_timestamp_falls_back_to_note_captured_at(tmp_path):
    # 計畫變更（owner default）：archive payload 常見 ended_at/timestamp 皆缺
    # （live 資料 ~3,056 筆），此時回退用 note 自己 frontmatter 的 captured_at
    # 當 ts，仍標 commit_source: backfill-approx，並在 summary 記 ts_source 的
    # 來源分佈供 operator 檢視。走真 git repo（不用 _fakes），驗證端到端。
    repo = tmp_path / "repo"
    head = _init_repo_with_commit(repo, "2026-08-01T00:00:00+00:00")
    # captured_at 固定在 _FM 為 "2026-08-17T03:32:41Z"，晚於上面的 commit 日期。
    a = _archive(tmp_path, "s1.json", {"cwd": str(repo), "ended_at": None})
    p = _note(tmp_path, "sl-1", "_unknown", a, "無引用\n")

    summary, warnings = pb.run(tmp_path, apply=True)

    assert summary["ts_source"] == {"captured_at": 1}
    assert summary["commit_candidates"] == 1 and summary["updated"] == 1
    assert summary["commit_reasons"] == {}
    fm, body = fio.read(p.read_text(encoding="utf-8"))
    assert fm["provenance"]["commit"] == head
    assert fm["provenance"]["commit_source"] == "backfill-approx"
    assert warnings == []


def test_captured_at_fallback_accepts_unquoted_yaml_datetime(tmp_path):
    # review round 2 #1：frontmatter_io.read() 用純 yaml.safe_load，未加引號的
    # captured_at（如 `_note_unquoted_captured_at` 寫出的那種）會被解析成
    # datetime.datetime，不是 str。舊版 `isinstance(captured_at, str)` 守門會把
    # 這種常見寫法誤判成 no-timestamp，回退整條路徑實際上永遠打不到。
    # 走真 git repo + 真 apply 端到端驗證，並補上這條路徑本身欠缺的冪等測試：
    # apply → dry-run 回報 0 → 再 apply 一次逐位元不變。
    repo = tmp_path / "repo"
    head = _init_repo_with_commit(repo, "2026-08-01T00:00:00+00:00")
    a = _archive(tmp_path, "s1.json", {"cwd": str(repo), "ended_at": None})
    p = _note_unquoted_captured_at(tmp_path, "sl-1", "_unknown", a, "2026-08-17T03:32:41Z", "無引用\n")

    summary1, warnings1 = pb.run(tmp_path, apply=True)
    assert summary1["ts_source"] == {"captured_at": 1}
    assert summary1["commit_candidates"] == 1 and summary1["updated"] == 1
    assert summary1["commit_reasons"] == {}
    assert warnings1 == []
    fm, _ = fio.read(p.read_text(encoding="utf-8"))
    assert fm["provenance"]["commit"] == head
    assert fm["provenance"]["commit_source"] == "backfill-approx"
    after_first_apply = p.read_bytes()

    summary2, warnings2 = pb.run(tmp_path, apply=False)
    assert summary2["commit_candidates"] == 0 and summary2["cites_candidates"] == 0
    assert warnings2 == []
    assert p.read_bytes() == after_first_apply

    summary3, warnings3 = pb.run(tmp_path, apply=True)
    assert summary3["updated"] == 0
    assert warnings3 == []
    assert p.read_bytes() == after_first_apply
