from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import instruction_corpus as ic
from paulsha_hippo.noise import build_corpus


class BuildCorpusTests(unittest.TestCase):
    def test_extracts_headings_and_content_lines_normalized(self):
        text = "## 6. 自主維護規則（agent-managed）\n- [multi_agent_devflow]   多行   空白\n\n  普通內容行  \n"
        corpus = build_corpus([text])
        self.assertIn("6. 自主維護規則（agent-managed）", corpus.headings)
        # whitespace is normalized for membership
        self.assertIn("- [multi_agent_devflow] 多行 空白", corpus.lines)
        self.assertIn("普通內容行", corpus.lines)
        # blank lines are not stored
        self.assertNotIn("", corpus.lines)

    def test_empty_corpus_is_falsy(self):
        self.assertFalse(build_corpus([]))
        self.assertTrue(build_corpus(["## H\ncontent line\n"]))


class DiscoverInstructionDocsTests(unittest.TestCase):
    def test_collects_doc_names_and_respects_skip_dirs_and_depth(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_text("a", encoding="utf-8")
            (root / "repo").mkdir()
            (root / "repo" / "AGENTS.md").write_text("b", encoding="utf-8")
            (root / "repo" / ".github").mkdir()
            (root / "repo" / ".github" / "GEMINI.md").write_text("c", encoding="utf-8")
            # must be skipped: inside .copilot / .git / node_modules / archive
            for skip in (".copilot", ".git", "node_modules", "archive"):
                (root / "repo" / skip).mkdir()
                (root / "repo" / skip / "AGENTS.md").write_text("nope", encoding="utf-8")
            # must be skipped: too deep (depth >= max_depth)
            deep = root / "repo" / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "AGENTS.md").write_text("toodeep", encoding="utf-8")

            found = {p.name for p in ic.discover_instruction_docs([root], max_depth=3)}
            paths = ic.discover_instruction_docs([root], max_depth=3)
            self.assertEqual({"CLAUDE.md", "AGENTS.md", "GEMINI.md"}, found)
            for p in paths:
                self.assertNotIn(".copilot", p.parts)
                self.assertNotIn(".git", p.parts)
                self.assertNotIn("node_modules", p.parts)
                self.assertNotIn("archive", p.parts)
                self.assertNotEqual("toodeep", p.read_text(encoding="utf-8"))

    def test_accepts_explicit_file_root(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "CLAUDE.md"
            f.write_text("x", encoding="utf-8")
            found = ic.discover_instruction_docs([f])
            self.assertEqual([f], found)

    def test_load_corpus_reads_discovered_docs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_text("## 動工前\n- [ ] 確認當前分支不是 main\n", encoding="utf-8")
            corpus = ic.load_corpus([root])
            self.assertIn("動工前", corpus.headings)
            self.assertIn("- [ ] 確認當前分支不是 main", corpus.lines)


if __name__ == "__main__":
    unittest.main()
