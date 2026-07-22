from __future__ import annotations

from paulsha_hippo.moc import frontmatter_io


def test_update_handles_exact_name_max_target_without_long_temp_name(tmp_path):
    # 255-byte filename is legal on the target filesystem; the temp basename
    # remains short and same-directory.
    name = "a" * 251 + ".md"
    path = tmp_path / name
    path.write_text("---\nslice_id: sl-a\n---\nbody\n", encoding="utf-8")
    frontmatter_io.update(path, {"title": "具體標題"})
    fm, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
    assert fm["title"] == "具體標題"
    assert body == "body\n"
    assert not list(tmp_path.glob(".hippo-fm-*"))
