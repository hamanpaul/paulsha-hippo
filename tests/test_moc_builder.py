# tests/test_moc_builder.py
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import faceout, moc_builder


def _slice(root: Path, slice_id: str, project: str, title: str) -> None:
    body = f"body {slice_id}\n"
    path = root / "knowledge" / project / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n"
                    f"artifact_kind: research\ntitle: {title}\n---\n{body}", encoding="utf-8")


def _slice_full(root: Path, slice_id: str, project: str, source_session: str,
                session_title: str, atom_title: str, distilled_from: str = "") -> None:
    path = root / "knowledge" / project / f"{atom_title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    distilled_line = f"distilled_from: {distilled_from}\n" if distilled_from else ""
    path.write_text(
        f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n"
        f"artifact_kind: report\nsource_session: {source_session}\n{distilled_line}"
        f'session_title: "{session_title}"\natom_title: "{atom_title}"\n---\nbody {slice_id}\n',
        encoding="utf-8")


class MocBuilderTests(unittest.TestCase):
    def test_three_mocs_with_moc_layer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "prplos-core", "alpha")
            _slice(root, "sl-2", "common-sense", "rule-x")
            moc_builder.build_mocs(root, now="2026-06-03T00:00:00Z")
            project_moc = (root / "knowledge" / "prplos-core-moc.md").read_text(encoding="utf-8")
            self.assertIn("memory_layer: moc", project_moc)
            # no session_title/atom_title → spine + atom both fall back to basename, nested
            self.assertIn("  - [[alpha--sl-1|alpha--sl-1]]", project_moc)
            cs = (root / "knowledge" / "common-sense-moc.md").read_text(encoding="utf-8")
            self.assertIn("  - [[rule-x--sl-2|rule-x--sl-2]]", cs)
            wiki = (root / "knowledge" / "wiki-moc.md").read_text(encoding="utf-8")
            self.assertIn("## Active", wiki)
            self.assertIn("  - [[alpha--sl-1|alpha--sl-1]]", wiki)

    def test_faceout_lists_decayed_not_deleted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "p", "alpha")
            moc_builder.build_mocs(root, now="2026-06-03T00:00:00Z")
            lifecycle.append_event(
                path=root / "runtime" / "ledger" / "lifecycle.jsonl",
                record_id="sl-1",
                event_type="decayed",
                source="janitor",
                reason="ttl_expired",
                actor="janitor"
            )
            faceout.mark_faceout(root)
            wiki = (root / "knowledge" / "wiki-moc.md").read_text(encoding="utf-8")
            self.assertIn("## Faceout", wiki)
            self.assertIn("sl-1", wiki)
            self.assertTrue((root / "knowledge" / "p" / "alpha--sl-1.md").exists())  # not deleted


class MocTwoLayerTests(unittest.TestCase):
    def test_atoms_nested_under_session_title(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice_full(root, "sl-a1", "prplos-core", "claude:s1", "修正啟動鏈", "OOM 風險")
            _slice_full(root, "sl-a2", "prplos-core", "claude:s1", "修正啟動鏈", "PYTHONPATH 修法")
            moc_builder.build_mocs(root, now="2026-06-17T00:00:00Z")
            moc = (root / "knowledge" / "prplos-core-moc.md").read_text(encoding="utf-8")
            self.assertIn("- 修正啟動鏈", moc)
            self.assertIn("  - [[OOM 風險--sl-a1|OOM 風險]]", moc)
            self.assertIn("  - [[PYTHONPATH 修法--sl-a2|PYTHONPATH 修法]]", moc)

    def test_missing_session_title_renders_without_crash(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-x", "p", "alpha")  # legacy helper: no session_title/atom_title
            moc_builder.build_mocs(root, now="2026-06-17T00:00:00Z")
            moc = (root / "knowledge" / "p-moc.md").read_text(encoding="utf-8")
            # no source_session + no session_title → neutral (未分組) spine, link still present
            self.assertIn("- (未分組)", moc)
            self.assertIn("alpha--sl-x", moc)

    def test_untitled_atom_falls_through_to_session_title(self):
        # #146: atom_title 'untitled' (title-gen failed) must be treated as no-title so the
        # meaningful session_title surfaces as the label instead of the literal 'untitled'.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice_full(root, "sl-u1", "prplos-core", "claude:s1", "修正 start.sh 啟動", "untitled")
            moc_builder.build_mocs(root, now="2026-06-17T00:00:00Z")
            moc = (root / "knowledge" / "prplos-core-moc.md").read_text(encoding="utf-8")
            self.assertIn("[[untitled--sl-u1|修正 start.sh 啟動]]", moc)
            self.assertNotIn("|untitled]]", moc)

    def test_wiki_keeps_project_attribution_and_no_cross_project_collapse(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # two slices in DIFFERENT projects sharing the same source_session
            _slice_full(root, "sl-w1", "prplos-core", "claude:s1", "啟動鏈調查", "OOM 風險")
            _slice_full(root, "sl-w2", "pwhm-core", "claude:s1", "FSM 追蹤", "FTA override")
            moc_builder.build_mocs(root, now="2026-06-17T00:00:00Z")
            wiki = (root / "knowledge" / "wiki-moc.md").read_text(encoding="utf-8")
            # 1. project attribution preserved on atom child lines
            self.assertIn("— prplos-core · report", wiki)
            self.assertIn("— pwhm-core · report", wiki)
            # 2. not collapsed under a single spine — both project session spines present
            self.assertIn("- 啟動鏈調查", wiki)
            self.assertIn("- FSM 追蹤", wiki)

    def test_same_session_id_different_agents_do_not_collide(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # same project + same bare source_session id, but captured by different agents:
            # distilled_from (agent:session) must keep them as separate spines.
            _slice_full(root, "sl-c", "prplos-core", "s1", "claude 那次", "A",
                        distilled_from="claude:s1")
            _slice_full(root, "sl-x", "prplos-core", "s1", "codex 那次", "B",
                        distilled_from="codex:s1")
            moc_builder.build_mocs(root, now="2026-06-17T00:00:00Z")
            moc = (root / "knowledge" / "prplos-core-moc.md").read_text(encoding="utf-8")
            self.assertIn("- claude 那次", moc)
            self.assertIn("- codex 那次", moc)


if __name__ == "__main__":
    unittest.main()
