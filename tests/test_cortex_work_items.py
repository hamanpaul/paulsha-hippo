from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORK_ITEMS_PATH = _REPO_ROOT / ".cortex" / "work-items.yaml"


def test_issue_146_work_item_links_point_to_existing_artifacts():
    document = yaml.safe_load(_WORK_ITEMS_PATH.read_text(encoding="utf-8"))
    work_item = document["work_items"]["issue-146-task-memory-payload"]
    path_links = [entry["ref"] for entry in work_item["links"] if entry["kind"] == "path"]

    assert (
        "openspec/changes/archive/2026-09-09-issue-146-task-memory-payload/proposal.md"
        in path_links
    )
    assert "openspec/changes/issue-146-task-memory-payload/proposal.md" not in path_links

    missing = [ref for ref in path_links if not (_REPO_ROOT / ref).exists()]
    assert missing == []
