import io, json
from contextlib import redirect_stderr, redirect_stdout
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


def _run_full(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


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


def test_ledger_write_failure_does_not_abort_print(tmp_path):
    """review round 1 / Important 1：read 歸因寫入失敗不可阻斷輸出。

    在 `<memory_root>/runtime/ledger` 放一個「檔案」（不是目錄），讓
    `usage_read.append_read_event` 內的 `mkdir(parents=True, exist_ok=True)`
    炸出 FileExistsError——`exist_ok=True` 只在最後一段路徑本來就是目錄時才
    生效，若是既存的非目錄檔案仍會拋例外，這在任何 uid（含 root）下都成立，
    比 chmod 更可靠。
    """
    p = _seed(tmp_path)
    wk = tmp_path / "runtime" / "wakeup"
    wk.mkdir(parents=True)
    (wk / "claude-code__s1.offered.json").write_text(
        json.dumps({"by_path": {str(p): "sl-aaaaaaaaaaaaaaaa"}, "by_id": {"sl-aaaaaaaaaaaaaaaa": str(p)}})
    )
    ledger_path = tmp_path / "runtime" / "ledger"
    ledger_path.write_text("not a dir", encoding="utf-8")

    rc, out, err = _run_full(
        ["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent",
         "--tool", "claude-code", "--session-id", "s1"]
    )

    assert rc == 0
    assert out.startswith("# Flash Layout\n")
    assert "warning" in err.lower()
    # 沒有部分寫入：runtime/ledger 仍是原本那個檔案，內容沒被動過。
    assert ledger_path.is_file() and ledger_path.read_text(encoding="utf-8") == "not a dir"


def test_default_view_prints_whole_file_byte_for_byte_and_no_read_event(tmp_path):
    """review round 1 / Important 2(a)：無 --agent 印整檔，不記 read 事件。"""
    _seed(tmp_path)
    rc, out = _run(["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path)])
    assert rc == 0
    assert out.encode("utf-8") == _NOTE.encode("utf-8")
    assert not (tmp_path / "runtime" / "ledger" / "memory_usage.jsonl").exists()


def test_tool_without_session_id_exits_2(tmp_path):
    """review round 1 / Important 2(b)：只給 --tool 不給 --session-id。"""
    _seed(tmp_path)
    rc, out, err = _run_full(
        ["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent", "--tool", "claude-code"]
    )
    assert rc == 2
    assert out == ""
    assert "--tool" in err and "--session-id" in err


def test_session_id_without_tool_exits_2(tmp_path):
    """review round 1 / Important 2(b)：只給 --session-id 不給 --tool。"""
    _seed(tmp_path)
    rc, out, err = _run_full(
        ["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent", "--session-id", "s1"]
    )
    assert rc == 2
    assert out == ""
    assert "--tool" in err and "--session-id" in err


def test_ambiguous_ref_exits_nonzero_prints_nothing(tmp_path):
    """review round 1 / Important 2(c)：兩筆 note 命中同一個 ref → ambiguous。"""
    kb = tmp_path / "knowledge"
    (kb / "proj1").mkdir(parents=True)
    (kb / "proj2").mkdir(parents=True)
    (kb / "proj1" / "a--sl-bbbbbbbbbbbbbbbb.md").write_text(_NOTE, encoding="utf-8")
    (kb / "proj2" / "b--sl-bbbbbbbbbbbbbbbb.md").write_text(_NOTE, encoding="utf-8")

    rc, out, err = _run_full(["show", "sl-bbbbbbbbbbbbbbbb", "--memory-root", str(tmp_path), "--agent"])

    assert rc != 0
    assert out == ""
    assert "show: sl-bbbbbbbbbbbbbbbb: ambiguous (2 match)" in err
