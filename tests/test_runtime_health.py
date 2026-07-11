"""#19 PR-C：doctor runtime 健康報告——/proc 掃描、非 canonical 標記、dream lock 狀態。"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import ops

_BTIME = 1751900000
_STARTTIME_TICKS = 5_000_000


def make_fake_proc(base: Path) -> Path:
    """建立可注入 proc_root 的假 /proc：頂層 stat 供 btime。"""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text(
        "cpu  0 0 0 0\n"
        f"btime {_BTIME}\n",
        encoding="ascii",
    )
    return proc


def add_fake_process(proc: Path, pid: int, argv: list[str], *,
                     cwd_target: Path | str | None,
                     starttime_ticks: int = _STARTTIME_TICKS) -> None:
    """寫入 /proc/<pid>/{cmdline,stat,cwd}。cwd_target 可為不存在路徑（dangling symlink）。"""
    pdir = proc / str(pid)
    pdir.mkdir()
    (pdir / "cmdline").write_bytes(b"\x00".join(a.encode("utf-8") for a in argv) + b"\x00")
    # /proc/<pid>/stat：')' 之後第 20 個 token（整行第 22 欄）= starttime
    after_paren = ["S"] + ["0"] * 18 + [str(starttime_ticks)] + ["0"] * 10
    (pdir / "stat").write_text(f"{pid} (python3) " + " ".join(after_paren) + "\n",
                               encoding="ascii")
    if cwd_target is not None:
        os.symlink(str(cwd_target), pdir / "cwd")


def expected_started_at() -> str:
    ticks = os.sysconf("SC_CLK_TCK")
    return datetime.fromtimestamp(
        _BTIME + _STARTTIME_TICKS // ticks, tz=timezone.utc).isoformat()


class ScanHippoProcessesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_finds_hippo_processes_with_pid_start_cmdline_cwd(self):
        home = Path.home()
        add_fake_process(
            self.proc, 4242,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run",
             "--memory-root", "/mem"],
            cwd_target=home)
        add_fake_process(self.proc, 4245, ["/usr/bin/sleep", "100"], cwd_target=home)

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual([r["pid"] for r in records], [4242])
        record = records[0]
        self.assertEqual(record["argv"][1:4], ["-m", "paulsha_hippo.cli", "dream"])
        self.assertIn("paulsha_hippo.cli dream run", record["cmdline"])
        self.assertEqual(record["started_at"], expected_started_at())
        self.assertEqual(record["cwd"], str(home))

    def test_scan_matches_hippo_console_script_and_excludes_self(self):
        home = Path.home()
        add_fake_process(self.proc, 4243, ["/usr/local/bin/hippo", "dream", "supervise"],
                         cwd_target=home)
        # 自身 PID 必須被排除（doctor 不把自己當孤兒）
        add_fake_process(self.proc, os.getpid(),
                         [sys.executable, "-m", "paulsha_hippo.cli", "doctor"],
                         cwd_target=home)

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual([r["pid"] for r in records], [4243])

    def test_malformed_stat_yields_unknown_started_at(self):
        home = Path.home()
        add_fake_process(self.proc, 4244,
                         [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
                         cwd_target=home)
        (self.proc / "4244" / "stat").write_text("garbage\n", encoding="ascii")

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual(records[0]["started_at"], "unknown")


class DreamProcessReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_orphan_with_foreign_interpreter_and_dead_cwd_is_non_canonical(self):
        add_fake_process(
            self.proc, 4242,
            ["/fake-venv/bin/python3", "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=self.base / "gone-worktree")  # dangling symlink
        add_fake_process(
            self.proc, 4243,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=Path.home())
        # 非 dream 的 hippo 進程（importer）不進 dream 報告
        add_fake_process(
            self.proc, 4244,
            [sys.executable, "-m", "paulsha_hippo.importer.cli", "ingest",
             "--queue-item", "/q.json", "--memory-root", "/mem"],
            cwd_target=Path.home())

        with mock.patch.object(ops.os, "kill",
                               side_effect=AssertionError("報告面不得發 signal")):
            reports = ops.dream_process_report(
                proc_root=self.proc, canonical_interpreter=sys.executable)

        by_pid = {r["pid"]: r for r in reports}
        self.assertEqual(set(by_pid), {4242, 4243})
        self.assertTrue(by_pid[4242]["non_canonical"])
        self.assertEqual(by_pid[4242]["reasons"], ["interpreter-mismatch", "cwd-missing"])
        self.assertFalse(by_pid[4243]["non_canonical"])
        self.assertEqual(by_pid[4243]["reasons"], [])

    def test_temp_worktree_cwd_is_flagged(self):
        worktree = self.base / ".psc_tmp" / "moc-abc123"
        worktree.mkdir(parents=True)
        add_fake_process(
            self.proc, 4242,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=worktree)

        reports = ops.dream_process_report(
            proc_root=self.proc, canonical_interpreter=sys.executable)

        self.assertTrue(reports[0]["non_canonical"])
        self.assertIn("cwd-temp-worktree", reports[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
