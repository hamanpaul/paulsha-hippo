from __future__ import annotations

import json

from paulsha_hippo.atomizer import slice_frontmatter
from paulsha_hippo.atomizer.llm_output import SliceProposal
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


def test_publication_survives_llm_produced_int_tag_and_passes_moc_index(tmp_path):
    # Issue #101 生產實證：cg 蒸餾 70-slice 大 session 產出未引號 issue 編號
    # tag（264，YAML int）。經 slice_frontmatter 正規化＋真實 publication 路徑
    # （PublicationTransaction prepare/commit）落地後，MOC index
    # （search.build_index / census.reconcile_index）的嚴格 list[str] tags
    # 驗證不得再判 invalid_frontmatter 排除整個 slice——原本會導致 dream
    # 每輪誤判 partial（sticky diagnostic 第五例）。
    proposal = SliceProposal(
        title="issue tracker note",
        artifact_kind="report",
        project="p",
        tags=(264, "router"),
        body="A durable note referencing issue 264.",
        source_fragment_indices=(0,),
        relations=(),
    )
    session_meta = {
        "source_agent": "claude",
        "source_session": "s-int-tag",
        "captured_at": "2026-07-30T00:00:00Z",
        "provenance": {"repo": "r", "commit": "c", "path": "p"},
    }
    built = slice_frontmatter.build_from_proposal(proposal, session_meta)
    assert built.frontmatter["tags"] == ["264", "router"]
    rendered = slice_frontmatter.render(built).encode("utf-8")

    target = tmp_path / "knowledge" / "p" / f"note--{built.slice_id}.md"
    tx = PublicationTransaction(
        tmp_path,
        publication_id="pub-int-tag",
        session_key="claude:s-int-tag",
        now="2026-07-30T00:00:00Z",
        config_hash="c",
    )
    tx.prepare(
        [PublicationItem(built.slice_id, target, rendered)],
        [],
        processing_extra={"accepted_slices": 1},
    )
    tx.commit()
    assert target.exists()

    coverage = search.build_index(tmp_path, link_weights={})
    assert coverage["invalid_frontmatter"] == 0
    assert coverage["eligible"] == 1
    assert coverage["indexed"] == 1
    assert not any("invalid tags" in w for w in coverage["warnings"]), coverage["warnings"]
    hits = search.search(tmp_path, "durable", project=None, limit=5, include_decayed=True)
    assert [h["slice_id"] for h in hits] == [built.slice_id]

    result = census.reconcile_index(tmp_path, coverage)
    assert result.ok, result.problems
    assert result.indexed_ids == {built.slice_id}
