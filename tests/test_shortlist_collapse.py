import json
from pathlib import Path
from paulsha_hippo.moc import search as S
from paulsha_hippo.hooks import _shortlist_common as SC
from paulsha_hippo import runtime_flags as rf


def _note(mr: Path, sid: str, title: str, at: str, body: str = "flash layout 說明"):
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True, exist_ok=True)
    (k / f"{sid}.md").write_text(f"---\nmemory_layer: knowledge\nslice_id: {sid}\nproject: proj\n"
                                 f"title: {title}\ncaptured_at: '{at}'\n---\n{body}\n", encoding="utf-8")


def _seed(mr: Path):
    _note(mr, "sl-old0000000000001", "Flash Layout", "2026-08-17T00:00:00Z")
    _note(mr, "sl-new0000000000002", "flash layout", "2026-08-21T00:00:00Z")
    _note(mr, "sl-oth0000000000003", "Release Button DIO24", "2026-08-01T00:00:00Z", body="flash layout 與 release button")
    S.build_index(mr, link_weights={})


def _ledger(mr: Path):
    return [json.loads(l) for l in (mr / "runtime" / "ledger" / "offered.jsonl").read_text().splitlines()]


def test_collapse_keeps_newest_and_records_collapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    assert "sl-new0000000000002" in out and "sl-old0000000000001" not in out
    ev = _ledger(tmp_path)[0]
    assert [o["sl_id"] for o in ev["offered"]] == ["sl-new0000000000002", "sl-oth0000000000003"]
    assert ev["collapsed"] == {"sl-new0000000000002": ["sl-old0000000000001"]}


def test_collapsed_note_not_offered_next_round_either(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    out2 = SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    assert "sl-old0000000000001" not in out2


def test_flag_off_restores_parallel_offer(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags(collapse_same_topic=False))
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "s2", cwd="/x", prompt="flash layout")
    assert "sl-old0000000000001" in out and "sl-new0000000000002" in out
    assert "collapsed" not in _ledger(tmp_path)[0]
