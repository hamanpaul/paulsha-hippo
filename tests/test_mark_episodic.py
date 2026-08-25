"""hippo knowledge mark-episodic：fix 4 migration，既存 knowledge note 一次性降層
episodic（可 revert），issue #136。仿 test_backfill_provenance.py / test_tags_migration.py
的 dry-run/apply/idempotent 慣例；分類邏輯完全借用 noise.episodic_reason（Task 13），
本檔只驗證 migration 本身（scan/plan/apply/revert）。
"""
from __future__ import annotations

from pathlib import Path

from paulsha_hippo import episodic_migration as em
from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io as fio

NOW = "2026-08-25T00:00:00Z"


def _note(mr: Path, sid: str, title: str, body: str) -> Path:
    p = mr / "knowledge" / "proj" / f"n--{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: proj\ntitle: \"{title}\"\n---\n{body}",
        encoding="utf-8",
    )
    return p


def test_dry_run_lists_without_writing(tmp_path):
    a = _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    b = _note(tmp_path, "sl-2", "Real SOP", "docker create --rm 建容器。\n")
    before = (a.read_bytes(), b.read_bytes())
    s, w = em.run(tmp_path, apply=False, now=NOW)
    assert s["pending"] == 1 and s["details"][0]["reason"] == "title:session-state" and s["updated"] == 0
    assert (a.read_bytes(), b.read_bytes()) == before and w == []


def test_apply_idempotent_and_revert(tmp_path):
    a = _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    s1, _ = em.run(tmp_path, apply=True, now=NOW)
    assert s1["updated"] == 1
    fm, body = fio.read(a.read_text())
    assert fm["memory_layer"] == "episodic" and body == "x\n"
    events = lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")
    assert events[-1]["event_type"] == "archived" and events[-1]["reason"] == "episodic"
    s2, _ = em.run(tmp_path, apply=True, now=NOW)
    assert s2["pending"] == 0 and s2["updated"] == 0
    assert em.revert(tmp_path, "sl-1", now=NOW) is True
    fm, _ = fio.read(a.read_text())
    assert fm["memory_layer"] == "knowledge" and "episodic_reason" not in fm
    assert lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")[-1]["event_type"] == "restored"
    assert em.revert(tmp_path, "sl-nope", now=NOW) is False


def test_non_knowledge_layer_untouched(tmp_path):
    # #136 binding constraint: run() only touches memory_layer == "knowledge".
    episodic_note = tmp_path / "knowledge" / "proj" / "n--sl-3.md"
    episodic_note.parent.mkdir(parents=True, exist_ok=True)
    episodic_note.write_text(
        "---\nslice_id: sl-3\nmemory_layer: episodic\nproject: proj\n"
        "title: \"session-handoff-already\"\n---\nx\n",
        encoding="utf-8",
    )
    before = episodic_note.read_bytes()
    s, w = em.run(tmp_path, apply=True, now=NOW)
    assert s["scanned"] == 0 and s["pending"] == 0 and s["updated"] == 0
    assert episodic_note.read_bytes() == before and w == []


def test_moc_and_unreadable_files_skipped_and_counted(tmp_path):
    # -moc.md index files are never note candidates; unreadable files are
    # skipped and reported into warnings (mirrors provenance_backfill.run).
    moc = tmp_path / "knowledge" / "proj" / "proj-moc.md"
    moc.parent.mkdir(parents=True, exist_ok=True)
    moc.write_text(
        "---\nslice_id: sl-moc\nmemory_layer: knowledge\nproject: proj\n"
        "title: \"handoff\"\n---\nx\n",
        encoding="utf-8",
    )
    bad = tmp_path / "knowledge" / "proj" / "n--sl-bad.md"
    bad.write_bytes(b"---\nslice_id: sl-bad\nmemory_layer: knowledge\n---\n\xff\xfe not utf-8\n")
    s, w = em.run(tmp_path, apply=False, now=NOW)
    assert s["scanned"] == 0 and s["pending"] == 0
    assert len(w) == 1 and str(bad) in w[0]


def test_apply_then_revert_is_byte_identical_to_original(tmp_path):
    # --revert must be exactly inverse: apply -> revert restores the file to
    # byte-identical original state (key order + values), not just equivalent
    # frontmatter. Uses fio.dump() to author the fixture (the realistic
    # production shape: real notes are always written through fio.dump/update,
    # never hand-typed raw text) so this isolates the round-trip property from
    # unrelated _scalar() quoting-style differences.
    p = tmp_path / "knowledge" / "proj" / "n--sl-1.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = {"slice_id": "sl-1", "memory_layer": "knowledge", "project": "proj",
          "title": "session-handoff-2026-08-12"}
    p.write_text(fio.dump(fm, "x\n"), encoding="utf-8")
    original = p.read_bytes()
    em.run(tmp_path, apply=True, now=NOW)
    assert em.revert(tmp_path, "sl-1", now=NOW) is True
    assert p.read_bytes() == original


def test_project_filter_scopes_scan(tmp_path):
    _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    other = tmp_path / "knowledge" / "other" / "n--sl-4.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text(
        "---\nslice_id: sl-4\nmemory_layer: knowledge\nproject: other\n"
        "title: \"handoff\"\n---\nx\n",
        encoding="utf-8",
    )
    s, _ = em.run(tmp_path, apply=False, now=NOW, project="other")
    assert s["scanned"] == 1 and s["pending"] == 1
    assert s["details"][0]["path"].endswith("sl-4.md")
