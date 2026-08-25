import json
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
