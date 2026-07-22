from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from paulsha_hippo import cli
from paulsha_hippo.dream import lock as dream_lock
from paulsha_hippo.ledger import dream

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-06-02T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha
"""


def _seed(root: Path):
    raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(_RAW, encoding="utf-8")


class DreamCliTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.atomizer.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--dry-run",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("dry_run"))
            self.assertIn("passes", payload)
            self.assertIn("atomize", payload["passes"])
            self.assertIn("janitor", payload["passes"])
            self.assertIsNone(dream.last_run(root))

    def test_require_idle_busy_skips(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.atomizer.cli.atomizer_config.load_config",
                return_value=(SimpleNamespace(default_promoter="identity"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            ), patch(
                "paulsha_hippo.dream.cli.janitor_config.load_config",
                return_value=(SimpleNamespace(), "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ), patch(
                "paulsha_hippo.dream.cli.idle.is_idle",
                return_value=False,
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--require-idle",
                        "--max-load",
                        "-1",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("skipped"), "system busy")
            self.assertEqual(payload.get("backlog_depth"), 1)
            self.assertIsNone(dream.last_run(root))

    def test_require_idle_low_memory_skips(self):
        from paulsha_hippo.lib import idle as idle_lib

        real_has_mem_headroom = idle_lib.has_mem_headroom

        def mem_gate(min_fraction, probe=None):
            self.assertEqual(min_fraction, 0.35)
            self.assertIsNotNone(probe)
            return real_has_mem_headroom(min_fraction, probe=probe)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.idle.is_idle",
                return_value=True,
            ), patch(
                "paulsha_hippo.dream.cli.idle._read_meminfo",
                side_effect=[
                    {"MemAvailable": 150, "MemTotal": 1000},
                    AssertionError("unexpected second meminfo read"),
                ],
            ), patch(
                "paulsha_hippo.dream.cli.idle.has_mem_headroom",
                side_effect=mem_gate,
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--require-idle",
                        "--min-avail-mem-pct",
                        "35.0",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(
                payload,
                {
                    "avail_pct": 15.0,
                    "backlog_depth": 1,
                    "skipped": "low memory",
                },
            )
            self.assertIsNone(dream.last_run(root))

    def test_dream_run_skips_when_lock_held(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            holder = dream_lock.acquire_dream_lock(root)
            self.assertIsNotNone(holder)
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root),
                                   "--now", "2026-07-10T00:00:00Z"])
            finally:
                holder.close()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("skipped"), "dream lock held by another process")
            self.assertIsNone(dream.last_run(root))

    def test_dream_run_propagates_non_contention_lock_error(self):
        # review F4：ENOLCK 等非 contention 錯誤不得被當成「another process」exit 0——
        # 必須上拋讓 dream 非零收場，故障可觀測。
        import errno

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            with patch(
                "paulsha_hippo.dream.cli.dream_lock.acquire_dream_lock",
                side_effect=OSError(errno.ENOLCK, "no locks available"),
            ):
                with self.assertRaises(OSError) as ctx:
                    cli.main(["dream", "run", "--memory-root", str(root),
                              "--now", "2026-07-10T00:00:00Z"])
            self.assertEqual(ctx.exception.errno, errno.ENOLCK)
            self.assertIsNone(dream.last_run(root))

    def test_dream_run_releases_lock_after_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            patches = dict(
                atomizer=patch(
                    "paulsha_hippo.atomizer.cli.atomizer_config.load_config",
                    return_value=(SimpleNamespace(default_promoter="identity"),
                                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                ),
                janitor=patch(
                    "paulsha_hippo.dream.cli.janitor_config.load_config",
                    return_value=(SimpleNamespace(),
                                  "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                ),
            )
            for round_now in ("2026-07-10T00:00:00Z", "2026-07-10T01:00:00Z"):
                buf = io.StringIO()
                with patches["atomizer"], patches["janitor"], redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root),
                                   "--now", round_now, "--dry-run"])
                self.assertEqual(rc, 0)
                payload = json.loads(buf.getvalue())
                self.assertNotIn("skipped", payload)  # 第二輪未被殘留鎖擋住
                self.assertIn("passes", payload)

    def test_status_reports_backlog(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["dream", "status", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["backlog_depth"], 1)
            self.assertIn("build_commit", payload["build_identity"])
            self.assertTrue(payload["config_identity"]["hash"])
            self.assertTrue(payload["config_identity"]["external_profiles"])
            self.assertTrue(all(
                "command_fingerprint" in row
                for row in payload["config_identity"]["external_profiles"]
            ))


if __name__ == "__main__":
    unittest.main()
