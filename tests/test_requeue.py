from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli, requeue
from paulsha_hippo.ledger import processing


def _park(root: Path, session_key: str, *, category: str = "invalid_output") -> None:
    processing.append_state(
        root, session_key=session_key, state="parked",
        now="2026-07-10T00:00:00Z", config_hash="cfg-hash",
        failure_category=category, attempts=6,
        cache_key=f"{session_key}__{'a' * 64}", error="boom",
    )


def _seed_fragment(root: Path, session_key: str) -> None:
    agent, _, session = session_key.partition(":")
    frag = root / "inbox" / "_slices" / "proj" / f"{agent}__{session}__000.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text("---\nfragment_index: 0\n---\nbody\n", encoding="utf-8")


class RequeueCoreTests(unittest.TestCase):
    def test_requeue_single_parked_session_returns_to_split(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:s1")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
                reason="backend fixed",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(summary["skipped"], [])
            self.assertEqual(
                summary["requeued"],
                [{"session_key": "claude:s1",
                  "previous_failure_category": "invalid_output",
                  "fragments": 1}],
            )
            event = processing.read_events(root)[-1]
            self.assertEqual(event["state"], "split")
            self.assertEqual(event["requeued_from"], "parked")
            self.assertEqual(event["requeue_reason"], "backend fixed")
            self.assertEqual(event["atomizer_config_hash"], "cfg-hash")

    def test_requeue_non_parked_session_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            processing.append_state(
                root, session_key="claude:s2", state="split",
                now="2026-07-10T00:00:00Z", config_hash="h",
            )
            summary = requeue.requeue(
                root, session_key="claude:s2", now="2026-07-10T01:00:00Z",
            )
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"], [{"session_key": "claude:s2", "reason": "split"}]
            )

    def test_requeue_unknown_session_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = requeue.requeue(
                root, session_key="claude:ghost", now="2026-07-10T01:00:00Z",
            )
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:ghost", "reason": "unknown session"}],
            )

    def test_requeue_all_parked_targets_only_parked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:p1", category="transient")
            _park(root, "codex:p2", category="backend_unavailable")
            processing.append_state(
                root, session_key="claude:live", state="split",
                now="2026-07-10T00:00:00Z", config_hash="h",
            )

            summary = requeue.requeue(root, all_parked=True, now="2026-07-10T01:00:00Z")

            self.assertEqual(
                [entry["session_key"] for entry in summary["requeued"]],
                ["claude:p1", "codex:p2"],
            )
            self.assertEqual(processing.state_of(root, "claude:p1"), "split")
            self.assertEqual(processing.state_of(root, "codex:p2"), "split")
            self.assertEqual(summary["skipped"], [])


class RequeueCliTests(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(argv)
        return rc, buf.getvalue()

    def test_cli_requeue_single(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            rc, out = self._run_cli(
                ["requeue", "claude:s1", "--memory-root", str(root),
                 "--now", "2026-07-10T01:00:00Z", "--reason", "backend fixed"]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["requeued"][0]["session_key"], "claude:s1")
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")

    def test_cli_requires_exactly_one_selector(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _ = self._run_cli(["requeue", "--memory-root", str(root)])
            self.assertEqual(rc, 2)
            rc2, _ = self._run_cli(
                ["requeue", "claude:s1", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc2, 2)

    def test_cli_exit_1_when_target_not_requeued(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out = self._run_cli(
                ["requeue", "claude:ghost", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out)["requeued"], [])

    def test_cli_all_parked_with_zero_parked_is_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out = self._run_cli(
                ["requeue", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"requeued": [], "skipped": []})


if __name__ == "__main__":
    unittest.main()
