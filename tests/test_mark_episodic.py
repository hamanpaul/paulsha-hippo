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
    assert fm["episodic_demoted_by"] == "mark-episodic"
    events = lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")
    assert events[-1]["event_type"] == "archived" and events[-1]["reason"] == "episodic"
    s2, _ = em.run(tmp_path, apply=True, now=NOW)
    assert s2["pending"] == 0 and s2["updated"] == 0
    reverted, message = em.revert(tmp_path, "sl-1", now=NOW)
    assert reverted is True and message == ""
    fm, _ = fio.read(a.read_text())
    assert fm["memory_layer"] == "knowledge"
    assert "episodic_reason" not in fm and "episodic_demoted_by" not in fm
    assert lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")[-1]["event_type"] == "restored"
    reverted, message = em.revert(tmp_path, "sl-nope", now=NOW)
    assert reverted is False and "not-found" in message


def test_revert_ignores_pipeline_demoted_note_without_marker(tmp_path):
    # review round 1 #1: Task 13's publish-time demotion (atomizer/pipeline.py)
    # writes the identical shape (memory_layer: episodic + episodic_reason,
    # episodic_reason schema-required for every episodic note) but never sets
    # episodic_demoted_by. --revert must not touch such a note: it is not this
    # migration's to restore.
    p = tmp_path / "knowledge" / "proj" / "n--sl-9.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\nslice_id: sl-9\nmemory_layer: episodic\nproject: proj\n"
        "episodic_reason: title:session-state\ntitle: \"session-handoff\"\n---\nx\n",
        encoding="utf-8",
    )
    before = p.read_bytes()
    reverted, message = em.revert(tmp_path, "sl-9", now=NOW)
    assert reverted is False
    assert "not-migrated" in message
    assert p.read_bytes() == before
    # no lifecycle event was written for a note that wasn't reverted
    assert lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl") == []


def test_ledger_append_failure_still_counts_update_and_warns(tmp_path, monkeypatch):
    # review round 1 #2: fio.update succeeding then lifecycle.append_event
    # raising must not lose the demotion from `updated`, must not raise out of
    # run(), and the warning must name the ledger (not "Failed to update") --
    # the note stays demoted on disk and revertible.
    a = _note(tmp_path, "sl-7", "session-handoff-2026-08-12", "x\n")

    def boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(em.lifecycle, "append_event", boom)
    s, w = em.run(tmp_path, apply=True, now=NOW)
    assert s["updated"] == 1
    assert len(w) == 1
    assert "ledger" in w[0] and str(a) in w[0]
    fm, body = fio.read(a.read_text())
    assert fm["memory_layer"] == "episodic" and body == "x\n"


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
    assert s["skipped_moc"] == 1
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
    reverted, message = em.revert(tmp_path, "sl-1", now=NOW)
    assert reverted is True and message == ""
    assert p.read_bytes() == original


def test_project_filter_scopes_scan(tmp_path):
    _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    other = tmp_path / "knowledge" / "other" / "n--sl-4.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    # 全支線 review I2：這筆 fixture 原本用裸字標題 `handoff`，靠的是舊版
    # 「標題一擊即中」規則。裸 `handoff` 現在只是弱訊號（body `x\n` 無強訊號佐證
    # → 不降層），本測試要驗的是 --project 過濾範圍、不是標題規則，故改用強形標題
    # 讓它照舊成為候選。
    other.write_text(
        "---\nslice_id: sl-4\nmemory_layer: knowledge\nproject: other\n"
        "title: \"session-handoff-2026-08-13\"\n---\nx\n",
        encoding="utf-8",
    )
    s, _ = em.run(tmp_path, apply=False, now=NOW, project="other")
    assert s["scanned"] == 1 and s["pending"] == 1
    assert s["details"][0]["path"].endswith("sl-4.md")
