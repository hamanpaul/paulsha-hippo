"""#176: dream 路徑接上 doc-fragment 語料（opt-in --instruction-root）。

行為契約：不帶 --instruction-root 時 dream run 行為與變更前完全一致
（空語料 -> doc-fragment 規則惰性 -> echo slice 照舊寫入、noise_dropped=0）。
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import patch

from paulsha_hippo import cli

_REPO_ROOT = Path(__file__).resolve().parents[1]

_DOC = """# fake-project 開發規範

## 分支政策
- 一律從 main 開 feature/<slug> 分支
- 禁止直接 push 到 main

## 測試政策
- 每個 PR 必須跑完整測試
"""

_ECHO_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s-echo
source_artifact: research
captured_at: "2026-07-02T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
## 分支政策
- 一律從 main 開 feature/<slug> 分支
- 禁止直接 push 到 main
"""

_REAL_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s-real
source_artifact: research
captured_at: "2026-07-02T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/y.md
---
# gh pr checks 的 CI 判定
gh 2.45.0 的 pr checks 沒有 --json，要用 pr view --json statusCheckRollup 判 CI。
"""


def _seed(root: Path, name: str, raw: str) -> None:
    path = root / "inbox" / "research" / "claude" / "2026-07-02" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


@contextmanager
def _isolated_home(root: Path):
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        yield


def _tmp_dir() -> TemporaryDirectory[str]:
    return TemporaryDirectory(dir=_REPO_ROOT)


class DreamCliInstructionRootPlumbingTests(unittest.TestCase):
    """dream/cli 佈線：--instruction-root -> corpus_for_roots -> pipeline.run(doc_corpus=...)."""

    def _run_with_captured_pipeline(self, extra_argv: list[str]) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "AGENTS.md"
            doc.write_text(_DOC, encoding="utf-8")
            captured: dict = {}

            def fake_run(memory_root, **kwargs):
                captured.update(kwargs)
                return {
                    "summary": {
                        "split_sessions": 0,
                        "slices": 0,
                        "skipped": 0,
                        "noise_dropped": 0,
                        "config_hash": "x",
                        "dry_run": True,
                    },
                    "warnings": [],
                }

            argv = [
                "dream",
                "run",
                "--memory-root",
                str(root),
                "--now",
                "2026-07-02T05:00:00Z",
                "--dry-run",
            ]
            argv += [arg.replace("__DOC__", str(doc)) for arg in extra_argv]
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.atomizer_pipeline.run",
                side_effect=fake_run,
            ), redirect_stdout(buf):
                rc = cli.main(argv)
            self.assertEqual(rc, 0)
            return captured

    def test_flag_builds_corpus_and_passes_doc_corpus(self):
        captured = self._run_with_captured_pipeline(["--instruction-root", "__DOC__"])
        corpus = captured.get("doc_corpus")
        self.assertTrue(corpus, "帶 --instruction-root 時 doc_corpus 必須為非空語料")
        self.assertIn("分支政策", corpus.headings)

    def test_no_flag_keeps_doc_fragment_rule_inert(self):
        captured = self._run_with_captured_pipeline([])
        self.assertFalse(
            captured.get("doc_corpus"),
            "不帶 --instruction-root 時 doc_corpus 必須為 falsy（規則惰性）",
        )


class DreamRunDocCorpusE2ETests(unittest.TestCase):
    """真 pipeline（identity promoter）端到端驗證 drop / 不 drop。"""

    def _dream_run(self, root: Path, extra_argv: list[str]) -> dict:
        buf = io.StringIO()
        with _isolated_home(root), redirect_stdout(buf):
            rc = cli.main(["dream",
                    "run",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-07-02T05:00:00Z",
                    "--promoter",
                    "identity",
                    *extra_argv,
                ]
            )
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def _knowledge_bodies(self, root: Path) -> list[str]:
        return [
            path.read_text(encoding="utf-8")
            for path in (root / "knowledge").rglob("*.md")
            if not path.name.endswith("-moc.md")
        ]

    def test_with_instruction_root_drops_doc_fragment_keeps_real(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            doc = root / "AGENTS.md"
            doc.write_text(_DOC, encoding="utf-8")
            _seed(root, "s-echo.md", _ECHO_RAW)
            _seed(root, "s-real.md", _REAL_RAW)
            payload = self._dream_run(root, ["--instruction-root", str(doc)])
            self.assertEqual(payload["passes"]["atomize"]["noise_dropped"], 1)
            bodies = self._knowledge_bodies(root)
            self.assertTrue(
                any("statusCheckRollup" in body for body in bodies),
                "真知識 slice 必須照常寫入 knowledge",
            )
            self.assertFalse(
                any("分支政策" in body for body in bodies),
                "doc-fragment slice 不得寫入 knowledge",
            )

    def test_without_instruction_root_behavior_unchanged(self):
        with _tmp_dir() as tmp:
            root = Path(tmp)
            _seed(root, "s-echo.md", _ECHO_RAW)
            payload = self._dream_run(root, [])
            self.assertEqual(payload["passes"]["atomize"]["noise_dropped"], 0)
            self.assertTrue(
                any("分支政策" in body for body in self._knowledge_bodies(root)),
                "不帶旗標時 doc-fragment 規則必須惰性、slice 照舊寫入",
            )


if __name__ == "__main__":
    unittest.main()
