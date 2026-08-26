from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from paulsha_hippo import cli, runtime_flags
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
            ), patch(
                "paulsha_hippo.dream.cli.idle.read_load1",
                return_value=3.14159,
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
            self.assertEqual(
                payload,
                {
                    "skipped": "system busy",
                    "load": 3.14,
                    "backlog_depth": 1,
                },
            )
            self.assertIsNone(dream.last_run(root))

    def test_require_idle_default_max_load_is_4(self):
        # 部署的 systemd unit 不帶 --max-load，argparse 預設就是生效值——
        # 這裡驗證未帶旗標時 is_idle 收到 max_load=4.0（本 PR 的核心路徑）。
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
            ) as mock_is_idle, patch(
                "paulsha_hippo.dream.cli.idle.read_load1",
                return_value=None,
            ), redirect_stdout(buf):
                rc = cli.main(["dream",
                        "run",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-06-02T05:00:00Z",
                        "--require-idle",
                    ]
                )
            self.assertEqual(rc, 0)
            mock_is_idle.assert_called_once_with(max_load=4.0)

    def test_require_idle_busy_skips_load_unavailable_is_null(self):
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
            ), patch(
                "paulsha_hippo.dream.cli.idle.read_load1",
                return_value=None,
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
            self.assertEqual(
                payload,
                {
                    "skipped": "system busy",
                    "load": None,
                    "backlog_depth": 1,
                },
            )
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

    def test_followups_failure_does_not_downgrade_dream(self):
        # 秘密字面量刻意選用 policy 的 github_pat 規則能命中的樣式（比照
        # test_dream_orchestrator.py::test_global_disable_rules_override_cannot_weaken_dream_ledger），
        # 這樣 assertNotIn 才是在驗證真正的 secret redaction，而不是巧合地對
        # 一句普通錯誤訊息（例如 "boom"）斷言其不出現。
        secret = "ghp_" + "A1b2C3d4" * 5
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _seed(root)
            with patch("paulsha_hippo.dream.cli.followups.verify",
                       side_effect=RuntimeError(f"token {secret} rejected")), \
                 patch("paulsha_hippo.dream.cli.load_flags", return_value=runtime_flags.HygieneFlags()):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root), "--promoter", "identity"])
            out = json.loads(buf.getvalue())
            self.assertEqual(rc, 0)
            self.assertIn(out["status"], ("ok", "partial"))
            self.assertIn("error", out["passes"]["followups"])
            self.assertNotIn(secret, json.dumps(out["passes"]["followups"]))   # sanitize

    def test_followups_fn_happy_path_runs_real_verify(self):
        # Review round 1 finding 2: the failure-isolation test above always mocks
        # followups.verify to raise — it never exercises the real followups_fn wiring
        # (real ledger + real projects.yaml -> followups.verify actually running and
        # succeeding). Build that real setup here: a real followups ledger entry whose
        # target file genuinely still contains its expected_stale text (-> verified_open),
        # and a real projects.yaml at the exact path dream/cli.py's followups_fn reads
        # (importer.config.default_projects_path(memory_root)) declaring a root that
        # contains that target file.
        from paulsha_hippo import followups as fu
        from paulsha_hippo.importer.config import default_projects_path

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # memory_root as a subdir of tmp (not tmp itself) so that
            # default_projects_path(root) == root.parent/"config"/"projects.yaml"
            # also lands inside tmp and is cleaned up with it (no host litter under /tmp).
            root = tmp_path / "memory"
            _seed(root)

            target_repo = tmp_path / "src-repo"
            target_repo.mkdir()
            (target_repo / "doc.md").write_text("stale marker here\n", encoding="utf-8")

            fu.append_event(
                root,
                {
                    "id": "fu-happy-1",
                    "event": "opened",
                    "slice_id": "sl-x",
                    "project": "paulshaclaw",
                    "target": {"path": "doc.md", "line": 1},
                    "expected_stale": "stale marker",
                    "claim": "c",
                    "source": "regex",
                },
                now="2026-07-10T00:00:00Z",
            )

            # Force "no PSC_CONFIG_ROOT override" regardless of the host environment,
            # so default_projects_path(root) resolves deterministically relative to
            # root and the projects.yaml written below is the one followups_fn reads.
            with patch.dict(os.environ, {"PSC_CONFIG_ROOT": ""}):
                projects_path = default_projects_path(root)
                projects_path.parent.mkdir(parents=True, exist_ok=True)
                projects_path.write_text(
                    "projects:\n"
                    "  paulshaclaw:\n"
                    "    roots:\n"
                    f"      - {target_repo}\n",
                    encoding="utf-8",
                )

                with patch(
                    "paulsha_hippo.dream.cli.load_flags",
                    return_value=runtime_flags.HygieneFlags(),
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = cli.main([
                            "dream", "run",
                            "--memory-root", str(root),
                            "--now", "2026-07-10T00:00:00Z",
                            "--promoter", "identity",
                        ])
            out = json.loads(buf.getvalue())
            self.assertEqual(rc, 0)
            followups_summary = out["passes"]["followups"]
            self.assertNotIn("error", followups_summary)
            self.assertNotIn("skipped", followups_summary)
            self.assertEqual(
                followups_summary,
                {"checked": 1, "verified_open": 1, "resolved": 0, "unverifiable": 0},
            )
            # followups running for real must not degrade dream's overall status.
            self.assertIn(out["status"], ("ok", "partial"))

    def test_followups_fn_resolves_roots_from_registry_only_project(self):
        """dream 的 followups 階段要走 registry-aware 的 union 讀取：只登記在
        generated registry（`project-hippo.yaml`）而沒進手寫 `projects.yaml` 的專案
        先前一律拿不到 root，每輪 dream 都只會替它記一筆 `no-root` unverifiable。
        """
        from paulsha_hippo import followups as fu
        from paulsha_hippo.importer import registry

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "memory"
            _seed(root)

            target_repo = tmp_path / "src-repo"
            target_repo.mkdir()
            (target_repo / "doc.md").write_text("stale marker here\n", encoding="utf-8")

            fu.append_event(
                root,
                {
                    "id": "fu-registry-1",
                    "event": "opened",
                    "slice_id": "sl-x",
                    "project": "paulshaclaw",
                    "target": {"path": "doc.md", "line": 1},
                    "expected_stale": "stale marker",
                    "claim": "c",
                    "source": "regex",
                },
                now="2026-07-10T00:00:00Z",
            )

            with patch.dict(os.environ, {"PSC_CONFIG_ROOT": ""}):
                # 只寫 generated registry，完全不寫手寫 projects.yaml。
                registry.record_discovery(
                    slug="paulshaclaw", roots=[str(target_repo)],
                    registry_path=registry.default_registry_path(root))

                with patch(
                    "paulsha_hippo.dream.cli.load_flags",
                    return_value=runtime_flags.HygieneFlags(),
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = cli.main([
                            "dream", "run",
                            "--memory-root", str(root),
                            "--now", "2026-07-10T00:00:00Z",
                            "--promoter", "identity",
                        ])
            out = json.loads(buf.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(
                out["passes"]["followups"],
                {"checked": 1, "verified_open": 1, "resolved": 0, "unverifiable": 0},
            )

    def test_status_reports_backlog(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.dream_ledger.backlog_census",
                side_effect=AssertionError("status must not rescan knowledge"),
            ), redirect_stdout(buf):
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
            self.assertIsNone(payload["health"])
            self.assertEqual(payload["health_source"], "unavailable")

    def test_status_reuses_last_run_health_without_full_census(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dream.append_run(root, {
                "ts": "2026-07-22T00:00:00Z",
                "status": "ok",
                "health": {"generic_title": 3, "invalid_checksum": 0},
            })
            buf = io.StringIO()
            with patch(
                "paulsha_hippo.dream.cli.dream_ledger.backlog_census",
                side_effect=AssertionError("status must not rescan knowledge"),
            ), redirect_stdout(buf):
                rc = cli.main(["dream", "status", "--memory-root", str(root)])

            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["health"], {"generic_title": 3, "invalid_checksum": 0})
            self.assertEqual(payload["health_source"], "last-run-ledger")


if __name__ == "__main__":
    unittest.main()
