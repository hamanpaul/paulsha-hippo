from __future__ import annotations

import json

from paulsha_hippo.atomizer.publication import PublicationItem, PublicationTransaction, recover_incomplete
from paulsha_hippo.ledger import processing
from paulsha_hippo.moc import census, search


def test_publication_commits_targets_edges_and_processing_once(tmp_path):
    target = tmp_path / "knowledge" / "p" / "a.md"
    tx = PublicationTransaction(tmp_path, publication_id="pub-1", session_key="claude:s1", now="2026-07-22T00:00:00Z", config_hash="c")
    tx.prepare(
        [PublicationItem("sl-a", target, b"---\nslice_id: sl-a\n---\nbody\n")],
        [{"type": "distilled_from", "from": "slice:sl-a", "to": "session:claude:s1"}],
        processing_extra={"accepted_slices": 1},
    )
    tx.commit()
    assert target.exists()
    assert processing.state_of(tmp_path, "claude:s1") == "promoted"
    assert len(processing.read_events(tmp_path)) == 1
    tx.commit()
    assert len(processing.read_events(tmp_path)) == 1


def test_recovery_finishes_materialized_prepare_without_duplicate_edges(tmp_path):
    target = tmp_path / "knowledge" / "p" / "a.md"
    tx = PublicationTransaction(tmp_path, publication_id="pub-2", session_key="codex:s2", now="2026-07-22T00:00:00Z", config_hash="c")
    data = b"---\nslice_id: sl-b\n---\nbody\n"
    tx.prepare([PublicationItem("sl-b", target, data)], [], processing_extra={"accepted_slices": 1})
    tx.materialize()
    result = recover_incomplete(tmp_path)
    assert result["recovered"] == ["pub-2"]
    assert processing.state_of(tmp_path, "codex:s2") == "promoted"
    assert recover_incomplete(tmp_path) == {"recovered": [], "rolled_back": []}


def test_recovery_rolls_back_partial_target_and_leaves_no_eligible_atom(tmp_path):
    target = tmp_path / "knowledge" / "p" / "a.md"
    tx = PublicationTransaction(tmp_path, publication_id="pub-3", session_key="agy:s3", now="2026-07-22T00:00:00Z", config_hash="c")
    tx.prepare(
        [PublicationItem("sl-c", target, b"new")],
        [],
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"other")
    result = recover_incomplete(tmp_path)
    assert result["rolled_back"] == ["pub-3"]
    assert target.read_bytes() == b"other"
    assert processing.state_of(tmp_path, "agy:s3") is None


def test_pending_publication_is_hidden_from_index_until_commit_marker(tmp_path):
    target = tmp_path / "knowledge" / "p" / "pending.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "slice_id: sl-pending\n"
        "memory_layer: knowledge\n"
        "project: p\n"
        "title: Durable concept\n"
        "tags: [concept]\n"
        "publication_id: pub-pending\n"
        "---\n"
        "A durable semantic concept body.\n",
        encoding="utf-8",
    )
    coverage = search.build_index(tmp_path, link_weights={})
    assert coverage["pool_excluded"] == {"publication-pending": 1}
    result = census.reconcile_index(tmp_path, coverage)
    assert result.ok, result.problems
    assert result.indexed_ids == set()

    journal = tmp_path / "runtime" / "ledger" / "publication.jsonl"
    journal.parent.mkdir(parents=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "publish_commit", "publication_id": "pub-pending"}) + "\n")
    coverage = search.build_index(tmp_path, link_weights={})
    assert coverage["eligible"] == 1
    assert search.search(tmp_path, "durable", project=None, limit=5, include_decayed=True)[0]["slice_id"] == "sl-pending"


def test_recovery_repairs_processing_after_durable_commit_marker(tmp_path):
    target = tmp_path / "knowledge" / "p" / "repair.md"
    tx = PublicationTransaction(
        tmp_path,
        publication_id="pub-repair",
        session_key="codex:s4",
        now="2026-07-22T00:00:00Z",
        config_hash="c",
    )
    tx.prepare(
        [PublicationItem("sl-repair", target, b"---\nslice_id: sl-repair\n---\nbody\n")],
        [],
        processing_extra={"accepted_slices": 1},
    )
    tx.materialize()
    processing.append_state(
        tmp_path,
        session_key="codex:s4",
        state="split",
        now="2026-07-22T00:00:00Z",
        config_hash="c",
    )
    journal = tmp_path / "runtime" / "ledger" / "publication.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "publish_commit", "publication_id": "pub-repair"}) + "\n")
    result = recover_incomplete(tmp_path)
    assert result["repaired"] == ["pub-repair"]
    assert processing.state_of(tmp_path, "codex:s4") == "promoted"
