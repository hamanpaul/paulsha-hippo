from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.hooks import _wakeup_common as wc


class WakeupOrientationTests(unittest.TestCase):
    def test_orientation_returned_and_no_session_offered_file(self):
        orientation = (
            "# 記憶 — proj\n\n記憶系統已啟用（本專案約 2 筆 knowledge）。"
            "與當前任務相關的記憶會在每次 prompt 後以短清單浮現；用 Read 開啟清單中列出的絕對路徑即取全文。"
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "paulsha_hippo.importer.project_resolver.resolve_project",
                return_value="proj",
            ), mock.patch(
                "paulsha_hippo.wakeup.builder.build_orientation",
                return_value=orientation,
            ):
                out = wc.compute_brief_and_record(root, "claude-code", "sess1", cwd="/x")
            # orientation is returned verbatim — no citation preamble prepended
            self.assertEqual(out, orientation)
            self.assertIn("Read", out)
            # SessionStart no longer writes a session-wide offered file
            self.assertFalse((root / "runtime" / "wakeup" / "claude-code__sess1.json").exists())

    def test_unresolved_project_returns_empty_and_no_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "paulsha_hippo.importer.project_resolver.resolve_project",
                return_value="_unknown",
            ):
                out = wc.compute_brief_and_record(root, "claude-code", "sess2", cwd="/x")
            self.assertEqual(out, "")
            self.assertFalse((root / "runtime" / "wakeup" / "claude-code__sess2.json").exists())


if __name__ == "__main__":
    unittest.main()
