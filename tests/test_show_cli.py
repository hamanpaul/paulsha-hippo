import io, json
from contextlib import redirect_stdout
from pathlib import Path
from paulsha_hippo import cli

_NOTE = (
    "---\nphase: review\nproject: proj\nslice_id: sl-aaaaaaaaaaaaaaaa\nartifact_kind: report\nversion: \"1\"\n"
    "created_at: \"2026-08-01 00:00:00\"\ncreated_by: codex\nsource_session: s0\ngate_required: false\n"
    "checksum: deadbeef\nmemory_layer: knowledge\nsource_agent: codex\ncaptured_at: \"2026-08-01 00:00:00\"\n"
    "supersedes: []\ndistilled_from: \"codex:s0\"\ntitle: \"Flash Layout\"\ntags:\n  - flash\n"
    "cites:\n  -\n    path: README-ARC.md\n    line: 108\n"
    "publication_id: pub1\nprovenance:\n  repo: \"r\"\n  commit: \"abc\"\n  path: \"/q.json\"\n  commit_source: hook\n"
    "distiller:\n  profile_id: claude\n  attempts: [{\"x\": 1}]\n---\n"
    "Layout 定義在 lds:35。\n\n第二段。\n"
)


def _seed(mr: Path) -> Path:
    p = mr / "knowledge" / "proj" / "flash-layout--sl-aaaaaaaaaaaaaaaa.md"
    p.parent.mkdir(parents=True); p.write_text(_NOTE, encoding="utf-8"); return p


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_agent_view_strips_machine_frontmatter(tmp_path):
    _seed(tmp_path)
    rc, out = _run(["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent"])
    assert rc == 0
    assert out.startswith("# Flash Layout\n")
    assert "commit: abc(hook)" in out and "cites: README-ARC.md:108" in out
    for banned in ("distiller", "checksum", "publication_id", "attempts", "distilled_from"):
        assert banned not in out
    body = "Layout 定義在 lds:35。\n\n第二段。\n"
    assert out.endswith(body)
    assert len(out.encode("utf-8")) <= len(body.encode("utf-8")) + 400


def test_agent_view_records_read_event_with_offered_flag(tmp_path):
    p = _seed(tmp_path)
    wk = tmp_path / "runtime" / "wakeup"; wk.mkdir(parents=True)
    (wk / "claude-code__s1.offered.json").write_text(
        json.dumps({"by_path": {str(p): "sl-aaaaaaaaaaaaaaaa"}, "by_id": {"sl-aaaaaaaaaaaaaaaa": str(p)}}))
    rc, _ = _run(["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent",
                  "--tool", "claude-code", "--session-id", "s1"])
    assert rc == 0
    ev = [json.loads(l) for l in (tmp_path / "runtime" / "ledger" / "memory_usage.jsonl").read_text().splitlines()]
    assert len(ev) == 1 and ev[0]["source"] == "read" and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa" and ev[0]["project"] == "proj"


def test_no_tool_session_no_event_and_path_ref_works(tmp_path):
    p = _seed(tmp_path)
    rc, out = _run(["show", str(p), "--memory-root", str(tmp_path), "--agent"])
    assert rc == 0 and "# Flash Layout" in out
    assert not (tmp_path / "runtime" / "ledger" / "memory_usage.jsonl").exists()


def test_unknown_ref_exits_1(tmp_path):
    rc, _ = _run(["show", "sl-nope", "--memory-root", str(tmp_path), "--agent"])
    assert rc == 1
