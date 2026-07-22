from __future__ import annotations

from pathlib import Path

from paulsha_hippo.atomizer import config, pipeline
from paulsha_hippo.ledger import dream, processing


def test_malformed_inbox_is_preserved_and_quarantined_once(tmp_path: Path):
    raw = tmp_path / "inbox" / "claude" / "bad.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("not valid frontmatter\n", encoding="utf-8")
    cfg, config_hash = config.load_config(override_path=None)

    first = pipeline.run(tmp_path, config=cfg, config_hash=config_hash, now="2026-07-22T00:00:00Z")
    quarantine = tmp_path / "runtime" / "quarantine" / "inbox"
    documents = list(quarantine.glob("*.md"))
    evidence = list(quarantine.glob("*.md.json"))

    assert not raw.exists()
    assert len(documents) == 1
    assert len(evidence) == 1
    assert "quarantined" in first["warnings"][0]

    second = pipeline.run(tmp_path, config=cfg, config_hash=config_hash, now="2026-07-22T01:00:00Z")

    assert second["warnings"] == []
    assert len(list(quarantine.glob("*.md"))) == 1
    assert dream.backlog_census(tmp_path)["quarantined"] == 1
    assert any(event["state"] == "quarantined" for event in processing.read_events(tmp_path))
