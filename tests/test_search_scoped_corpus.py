from __future__ import annotations

import logging
from pathlib import Path

from paulsha_hippo.moc import runner, search


def _slice(memory_root: Path, project: str, slice_id: str, title: str, body: str) -> None:
    path = memory_root / "knowledge" / project / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"slice_id: {slice_id}\n"
        "memory_layer: knowledge\n"
        f"project: {project}\n"
        f"title: {title}\n"
        "artifact_kind: spec\n"
        "captured_at: 2026-07-06T00:00:00Z\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _write_projects_yaml(memory_root: Path, projects: dict[str, list[Path]]) -> None:
    config_path = memory_root.parent / "config" / "projects.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["version: 1", "projects:"]
    for slug, roots in projects.items():
        lines.append(f"  {slug}:")
        lines.append("    roots:")
        for root in roots:
            lines.append(f"      - {root}")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cross_project_doc_overlap_stays_indexed(tmp_path, monkeypatch):
    memory_root = tmp_path / "memory"
    instruction_root = tmp_path / "root-a"
    instruction_root.mkdir()
    body = "## Arch\nRPC routing detail line one\nline two"
    (instruction_root / "AGENTS.md").write_text(body + "\n", encoding="utf-8")
    _slice(memory_root, "proj-b", "sl-proj-b", "proj-b-shared", body)
    _write_projects_yaml(memory_root, {"proj-a": [instruction_root]})
    monkeypatch.setattr(
        "paulsha_hippo.instruction_corpus.default_roots",
        lambda: [instruction_root],
    )

    runner.run_moc(memory_root, now="2026-07-06T00:00:00Z")

    hits = search.search(memory_root, "RPC", project="proj-b", limit=5, include_decayed=True)
    assert [hit["slice_id"] for hit in hits] == ["sl-proj-b"]


def test_missing_roots_means_zero_exclusion(tmp_path):
    memory_root = tmp_path / "memory"
    _slice(memory_root, "proj-x", "sl-proj-x", "unique-note", "unique body content")

    stats = search.build_index(memory_root, link_weights={})

    project_stats = stats.per_project["proj-x"]
    assert project_stats.excluded == 0
    assert project_stats.indexed == 1
    hits = search.search(memory_root, "unique", project="proj-x", limit=5, include_decayed=True)
    assert [hit["slice_id"] for hit in hits] == ["sl-proj-x"]


def test_exclude_rate_warns_over_40pct(tmp_path, caplog):
    memory_root = tmp_path / "memory"
    instruction_root = tmp_path / "root-a"
    instruction_root.mkdir()
    shared = "## Arch\nscoped corpus detail line one\nline two"
    (instruction_root / "AGENTS.md").write_text(shared + "\n", encoding="utf-8")
    _write_projects_yaml(memory_root, {"proj-a": [instruction_root]})
    _slice(memory_root, "proj-a", "sl-noise", "shared-doc", shared)
    _slice(memory_root, "proj-a", "sl-keep", "kept-doc", "project unique retrieval note")

    with caplog.at_level(logging.WARNING, logger="paulsha_hippo.moc.search"):
        stats = search.build_index(memory_root, link_weights={})

    project_stats = stats.per_project["proj-a"]
    assert project_stats.indexed == 1
    assert project_stats.excluded == 1
    assert project_stats.exclude_rate == 0.5
    assert any("proj-a" in record.message and "exclude_rate=0.50" in record.message
               for record in caplog.records)
    assert any("proj-a" in warning and "exclude_rate=0.50" in warning for warning in stats.warnings)
