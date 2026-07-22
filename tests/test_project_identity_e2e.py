from __future__ import annotations

from paulsha_hippo.atomizer.config import project_directory_key


def test_rich_project_ids_keep_metadata_boundary_and_avoid_legacy_collision():
    first = project_directory_key("github.com/a/b")
    second = project_directory_key("github.com__a__b")
    assert first != second
    assert first.startswith("github.com-a-b--p-")
    assert second == "github.com__a__b"


def test_simple_project_directory_key_preserves_existing_layout():
    assert project_directory_key("paulshaclaw") == "paulshaclaw"
