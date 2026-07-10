# tests/test_recall_cli.py — hippo recall（跨 CLI consumer API，契約 5）
import json
from pathlib import Path

from paulsha_hippo import cli
from paulsha_hippo.hooks import _shortlist_common as SC
from paulsha_hippo.moc import search as S


def _seed(mr: Path):
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def test_recall_prints_shortlist_and_records_offered_with_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    rc = cli.main(["recall", "--memory-root", str(tmp_path), "--cwd", "/x",
                   "--tool", "codex", "--session-id", "sidR", "--prompt", "SerialWrap 執行"])
    assert rc == 0
    out = capsys.readouterr().out
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    assert note in out and "Read" in out
    events = [json.loads(l) for l in
              (tmp_path / "runtime" / "ledger" / "offered.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(events) == 1
    assert events[0]["tool"] == "codex"           # offered 事件 tool attribution
    assert events[0]["session_id"] == "sidR"
    assert events[0]["offered"] == [{"sl_id": "sl-aaaaaaaaaaaaaaaa", "path": note}]
    m = json.loads((tmp_path / "runtime" / "wakeup" / "codex__sidR.offered.json").read_text())
    assert m["by_id"]["sl-aaaaaaaaaaaaaaaa"] == note


def test_recall_no_match_prints_nothing_exit0(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    rc = cli.main(["recall", "--memory-root", str(tmp_path), "--cwd", "/x",
                   "--tool", "codex", "--session-id", "s", "--prompt", "zzzznomatch"])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_recall_missing_required_flags_exit2(capsys):
    assert cli.main(["recall"]) == 2


def test_recall_traversal_tool_rejected_exit2(tmp_path, capsys):
    # 迴歸（#17 review [high]）：--tool 夾帶路徑分隔符時，過去會把 offered map
    # 原子 replace 到 memory root 之外——argparse 層直接拒絕（exit 2），零落檔。
    rc = cli.main(["recall", "--memory-root", str(tmp_path), "--cwd", "/x",
                   "--tool", "../../../outside", "--session-id", "s", "--prompt", "x"])
    assert rc == 2
    assert "invalid tool" in capsys.readouterr().err
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path.parent / "outside__s.offered.json").exists()
