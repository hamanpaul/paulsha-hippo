from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import record_source


_KNOWLEDGE = """---
memory_layer: knowledge
slice_id: sl-1
supersedes:
  - sl-0
project: paulshaclaw
source_agent: claude
source_session: sess-abc
source_artifact: a.md
captured_at: "2026-01-01T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: deadbeef
  path: docs/x.md
---
body
"""

_INBOX = """---
memory_layer: inbox
slice_id: sl-9
source_agent: claude
source_session: sess-z
captured_at: "2026-01-01T00:00:00Z"
---
body
"""

_NO_SLICE = """---
memory_layer: knowledge
source_agent: claude
source_session: sess-q
captured_at: "2026-01-01T00:00:00Z"
---
body
"""

_MALFORMED_OPTIONAL_FIELDS = """---
memory_layer: knowledge
slice_id: " sl-2 "
supersedes:
  - sl-1
  - true
  - 123
  - {nested: dict}
  - null
  - ""
  - "  "
  - sl-0
source_agent:
  name: claude
source_session: 123
captured_at:
  year: 2026
provenance:
  repo: paulshaclaw
  commit: null
  path:
    - docs/x.md
---
body
"""

_WHITESPACE_SLICE = """---
memory_layer: knowledge
slice_id: "   "
source_agent: claude
source_session: sess-space
captured_at: "2026-01-01T00:00:00Z"
---
body
"""


def _write(root: Path, name: str, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


class RecordSourceTests(unittest.TestCase):
    def test_reads_knowledge_record_fields(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "a.md", _KNOWLEDGE)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(warnings, [])
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec.record_id, "sl-1")
            self.assertEqual(rec.supersedes, ("sl-0",))
            self.assertEqual(rec.source_key, "claude:sess-abc")
            self.assertEqual(rec.captured_at, "2026-01-01T00:00:00Z")
            self.assertEqual(rec.provenance["path"], "docs/x.md")

    def test_provenance_is_immutable(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "a.md", _KNOWLEDGE)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(warnings, [])
            with self.assertRaises(TypeError):
                records[0].provenance["path"] = "tampered.md"

    def test_skips_non_knowledge_layer(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "b.md", _INBOX)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(records, [])
            self.assertEqual(warnings, [])

    def test_missing_slice_id_warns_and_skips(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "c.md", _NO_SLICE)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(records, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("slice_id", warnings[0])

    def test_whitespace_slice_id_warns_and_skips(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "space.md", _WHITESPACE_SLICE)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(records, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("slice_id", warnings[0])

    def test_sanitizes_malformed_optional_fields(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "optional.md", _MALFORMED_OPTIONAL_FIELDS)
            records, warnings = record_source.iter_records(kroot)
            self.assertEqual(warnings, [])
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec.record_id, "sl-2")
            self.assertEqual(rec.supersedes, ("sl-1", "sl-0"))
            self.assertEqual(rec.source_key, "_unknown:_unknown")
            self.assertEqual(rec.captured_at, "")
            self.assertEqual(rec.provenance, {"repo": "paulshaclaw"})

    def test_skips_symlinked_markdown_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            external = root / "outside.md"
            external.write_text(_KNOWLEDGE, encoding="utf-8")
            kroot.mkdir(parents=True, exist_ok=True)
            (kroot / "linked.md").symlink_to(external)

            records, warnings = record_source.iter_records(kroot)

            self.assertEqual(records, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("symlink", warnings[0])

    def test_skips_files_over_size_limit(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "large.md", _KNOWLEDGE + ("x" * 128))
            old_limit = getattr(record_source, "MAX_RECORD_FILE_BYTES", None)
            record_source.MAX_RECORD_FILE_BYTES = 32
            try:
                records, warnings = record_source.iter_records(kroot)
            finally:
                if old_limit is None:
                    delattr(record_source, "MAX_RECORD_FILE_BYTES")
                else:
                    record_source.MAX_RECORD_FILE_BYTES = old_limit

            self.assertEqual(records, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("file too large", warnings[0])

    def test_duplicate_slice_id_warns_and_skips_later_record(self):
        with TemporaryDirectory() as tmp:
            kroot = Path(tmp) / "knowledge"
            _write(kroot, "a.md", _KNOWLEDGE)
            _write(kroot, "b.md", _KNOWLEDGE.replace("source_session: sess-abc", "source_session: sess-duplicate"))

            records, warnings = record_source.iter_records(kroot)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source_key, "claude:sess-abc")
            self.assertEqual(len(warnings), 1)
            self.assertIn("duplicate slice_id", warnings[0])

    def test_missing_root_returns_empty(self):
        with TemporaryDirectory() as tmp:
            records, warnings = record_source.iter_records(Path(tmp) / "nope")
            self.assertEqual(records, [])
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
