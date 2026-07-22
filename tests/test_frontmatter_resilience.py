"""Regression for #139: frontmatter write-path escaping + parse-path resilience.

Two failure classes broke the dream pipeline (6/22–6/23):
- write-path: title / session_title with a leading flow indicator ('[', '{') or
  embedded quotes serialized to invalid YAML.
- parse-path: a single malformed-frontmatter file raised ParserError and aborted
  the whole atomize / moc pass instead of being skipped.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import pipeline
from paulsha_hippo.importer.frontmatter import render_markdown
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.moc import moc_builder


def _read_frontmatter_block(markdown: str) -> dict:
    """Parse the leading --- frontmatter block exactly as a YAML consumer would."""
    lines = markdown.splitlines()
    assert lines[0].strip() == "---"
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end])) or {}


_GOOD_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: good1
source_artifact: research
captured_at: "2026-06-23T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha body
"""

# Real-world poison pill: title value starts with '[' -> YAML flow sequence never closed.
_MALFORMED_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: bad1
source_artifact: session
title: [PERSONA CONTRACT —
captured_at: "2026-06-23T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/y.md
---
# Topic B
beta body
"""


class ImporterEscapingTests(unittest.TestCase):
    def test_bracket_leading_title_is_quoted_and_parseable(self):
        session = {
            "tool": "claude-code",
            "session_id": "s-bracket",
            "assistant_summary": "semantic outcome remains separate",
            "session_title": "[PERSONA CONTRACT — guardrail",
            "title_source": "fallback",
        }
        markdown = render_markdown(session, project="paulshaclaw")
        fm = _read_frontmatter_block(markdown)
        self.assertEqual(fm["title"], "[PERSONA CONTRACT — guardrail")

    def test_json_like_title_with_embedded_quotes_is_parseable(self):
        session = {
            "tool": "codex",
            "session_id": "s-json",
            "assistant_summary": "semantic outcome remains separate",
            "session_title": '{"verdict":"needs-attention"}',
            "title_source": "external-agent",
        }
        markdown = render_markdown(session, project="serialwrap")
        fm = _read_frontmatter_block(markdown)
        self.assertEqual(fm["title"], '{"verdict":"needs-attention"}')


class FrontmatterIoEscapingTests(unittest.TestCase):
    def test_dump_escapes_embedded_quotes_round_trip(self):
        value = '{"verdict":"needs-at'
        text = fio.dump({"memory_layer": "knowledge", "session_title": value}, "body\n")
        fm, body = fio.read(text)
        self.assertEqual(fm["session_title"], value)
        self.assertEqual(body, "body\n")

    def test_dump_quotes_bracket_leading_value_round_trip(self):
        value = "[PERSONA CONTRACT —"
        text = fio.dump({"memory_layer": "knowledge", "title": value}, "body\n")
        fm, _ = fio.read(text)
        self.assertEqual(fm["title"], value)


class AtomizeResilienceTests(unittest.TestCase):
    def test_malformed_inbox_doc_is_skipped_not_fatal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "inbox" / "research" / "claude" / "2026-06-23"
            base.mkdir(parents=True, exist_ok=True)
            (base / "good1.md").write_text(_GOOD_RAW, encoding="utf-8")
            bad = root / "inbox" / "sessions" / "claude" / "2026-06-23" / "bad1.md"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text(_MALFORMED_RAW, encoding="utf-8")

            cfg, h = atomizer_config.load_config(override_path=None)
            # Must NOT raise — one poison-pill file cannot abort the whole pass.
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-06-23T03:00:00Z")

            # Good doc still produced a knowledge slice.
            self.assertTrue(list((root / "knowledge").rglob("*.md")))
            # Bad doc is recorded as skipped, not crashed.
            self.assertTrue(any("bad1.md" in w for w in result["warnings"]))


class MocResilienceTests(unittest.TestCase):
    def test_malformed_knowledge_slice_is_skipped_not_fatal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "knowledge" / "paulshaclaw" / "alpha--sl-good.md"
            good.parent.mkdir(parents=True, exist_ok=True)
            good.write_text(
                "---\nslice_id: sl-good\nmemory_layer: knowledge\nproject: paulshaclaw\n"
                "artifact_kind: research\ntitle: alpha\n---\nbody\n", encoding="utf-8")
            bad = root / "knowledge" / "serialwrap" / "report--sl-bad.md"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text(
                "---\nslice_id: sl-bad\nmemory_layer: knowledge\nproject: serialwrap\n"
                'session_title: "{"verdict":"needs-at"\n---\nbody\n', encoding="utf-8")

            # Must NOT raise.
            moc_builder.build_mocs(root, now="2026-06-23T03:00:00Z")
            project_moc = (root / "knowledge" / "paulshaclaw-moc.md").read_text(encoding="utf-8")
            self.assertIn("alpha--sl-good", project_moc)


if __name__ == "__main__":
    unittest.main()
