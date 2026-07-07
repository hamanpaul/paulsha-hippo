import io
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from paulsha_hippo import cli as memory_cli
from paulsha_hippo.syncback import gate
from paulsha_hippo.syncback import cli as syncback_cli


@contextmanager
def _repo_tempdir():
    root = (
        Path(__file__).resolve().parents[1]
        / ".scratch_task4"
        / f"test-syncback-cli-{uuid.uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _seed(repo_root: Path, *, mergeable: bool) -> None:
    evidence_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "README.md").write_text("idx\n", encoding="utf-8")
    (evidence_dir / "stage2-integration-template.md").write_text("template\n", encoding="utf-8")
    review_body = "- 結論：可合併。\n- 無阻斷性問題。\n" if mergeable else "- 結論：有阻斷性問題，不可合併。\n"
    (evidence_dir.parent / "review.md").write_text(f"## 結論\n\n{review_body}", encoding="utf-8")


class SyncbackCliTest(unittest.TestCase):
    def test_run_returns_rc0_when_all_checks_pass(self) -> None:
        with _repo_tempdir() as repo_root:
            _seed(repo_root, mergeable=True)
            buf = io.StringIO()

            with redirect_stdout(buf):
                rc = syncback_cli.main(
                    ["check", "--repo-root", str(repo_root), "--now", "2026-06-06T00:00:00Z"],
                    _test_runner=lambda modules: True,
                )

            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("PASS", out)
            for condition_id in ("tests", "decay_evidence", "evidence_present", "review_clear", "schema_unextended"):
                self.assertIn(condition_id, out)
            self.assertIn("sync manifest", out)
            self.assertIn(gate.SYNC_MANIFEST[0], out)

    def test_run_returns_rc1_when_review_is_blocking(self) -> None:
        with _repo_tempdir() as repo_root:
            _seed(repo_root, mergeable=False)
            buf = io.StringIO()

            with redirect_stdout(buf):
                rc = syncback_cli.main(
                    ["check", "--repo-root", str(repo_root), "--now", "2026-06-06T00:00:00Z"],
                    _test_runner=lambda modules: True,
                )

            out = buf.getvalue()
            self.assertEqual(rc, 1)
            self.assertIn("FAIL", out)
            self.assertIn("review_clear", out)
            self.assertNotIn("sync manifest", out)

    def test_json_output_path_works(self) -> None:
        verdict = gate.GateVerdict(
            ok=True,
            ts="2026-06-06T00:00:00Z",
            conditions=(
                gate.ConditionResult(id="tests", name="tests", passed=True, detail=""),
            ),
            sync_manifest=("paulshaclaw/memory/",),
        )
        buf = io.StringIO()

        with (
            mock.patch("paulsha_hippo.syncback.cli.evaluate_gate", return_value=verdict) as evaluate,
            redirect_stdout(buf),
        ):
            rc = memory_cli.main(["syncback", "check", "--repo-root", ".", "--now", "2026-06-06T00:00:00Z", "--json"]
            )

        payload = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ts"], "2026-06-06T00:00:00Z")
        self.assertEqual(payload["sync_manifest"], ["paulshaclaw/memory/"])
        evaluate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
