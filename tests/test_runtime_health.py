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

    def test_sibling_venv_sharing_base_interpreter_is_interpreter_mismatch(self):
        """回歸（#19）：兩個獨立 venv 共用同一 base interpreter 時，各自 bin/python3
        都 symlink 到同一真實檔——對整條 argv[0] 做 resolve 會收斂成同一 parent、
        interpreter-mismatch 永不觸發（venv-symlink 最常見情境）。判定必須落在 venv
        的 bin 目錄層：canonical 與孤兒各在不同 venv → 標記 interpreter-mismatch。

        cwd 指向存在且非暫存的目錄，隔離出「唯一 reason 只能來自 interpreter 比對」
        （不存在的假路徑 fixture 因 resolve 不收斂而覆蓋不到本情境）。
        """
        base_python = self.base / "base-python" / "bin" / "python3"
        base_python.parent.mkdir(parents=True)
        base_python.touch()
        canonical_bin = self.base / "venv-canonical" / "bin"
        orphan_bin = self.base / "venv-orphan" / "bin"
        canonical_bin.mkdir(parents=True)
        orphan_bin.mkdir(parents=True)
        os.symlink(base_python, canonical_bin / "python3")
        os.symlink(base_python, orphan_bin / "python3")
        # sanity：兩個 venv 的 python3 對整條路徑 resolve 後確實收斂到同一 parent
        self.assertEqual(
            (canonical_bin / "python3").resolve().parent,
            (orphan_bin / "python3").resolve().parent)

        # cwd 用 Path.home()：存在且非暫存區，隔離出唯一 reason 只能來自 interpreter
        # 比對（self.base 在 tempdir 下會另外觸發 cwd-temp-worktree）。
        add_fake_process(
            self.proc, 4242,
            [str(orphan_bin / "python3"), "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=Path.home())

        reports = ops.dream_process_report(
            proc_root=self.proc,
            canonical_interpreter=str(canonical_bin / "python3"))

        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0]["non_canonical"])
        self.assertEqual(reports[0]["reasons"], ["interpreter-mismatch"])

    def test_same_venv_interpreter_is_canonical(self):
        """對照：孤兒與 canonical 同一 venv（同一 bin/python3）→ 不標記 mismatch。"""
        venv_bin = self.base / "venv-shared" / "bin"
        venv_bin.mkdir(parents=True)
        (self.base / "base-python").mkdir()
        base_python = self.base / "base-python" / "python3"
        base_python.touch()
        os.symlink(base_python, venv_bin / "python3")

        add_fake_process(
            self.proc, 4242,
            [str(venv_bin / "python3"), "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=Path.home())

        reports = ops.dream_process_report(
            proc_root=self.proc,
            canonical_interpreter=str(venv_bin / "python3"))

        self.assertEqual(len(reports), 1)
        self.assertFalse(reports[0]["non_canonical"])
        self.assertEqual(reports[0]["reasons"], [])


class DreamLockStatusTest(unittest.TestCase):
    def test_absent_free_held(self):
        import fcntl as _fcntl

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # absent：契約 3 路徑 <memory_root>/runtime/locks/dream.lock 尚未存在
            self.assertEqual(ops.dream_lock_status(root), "absent")

            lock_path = root / "runtime" / "locks" / "dream.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.touch()
            self.assertEqual(ops.dream_lock_status(root), "free")

            with lock_path.open("a+", encoding="utf-8") as holder:
                _fcntl.flock(holder, _fcntl.LOCK_EX)  # 模擬 PR-A dream run 整輪持鎖
                self.assertEqual(ops.dream_lock_status(root), "held")
            self.assertEqual(ops.dream_lock_status(root), "free")


class DoctorRuntimeHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)
        self.memory_root = self.base / "memory"
        (self.memory_root / "runtime" / "locks").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_doctor_reports_dream_lock_and_identifies_fake_orphan(self):
        """驗收（spec §3.4）：doctor 能識別偽造的孤兒進程 fixture——只報告，不 kill。

        偽造 pid 4242 非真實進程；若實作誤發 signal 會 ProcessLookupError 直接紅燈。
        """
        add_fake_process(
            self.proc, 4242,
            ["/fake-venv/bin/python3", "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=self.base / "gone-worktree")
        (self.memory_root / "runtime" / "locks" / "dream.lock").touch()

        env = {"HIPPO_MEMORY_ROOT": str(self.memory_root),
               "PSC_MEMORY_ROOT": str(self.memory_root)}
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env), redirect_stdout(buffer):
            # 不斷言 return code：PR-A 的 backend probe 段落可能因環境無 backend 而非零；
            # 本 task 的段落是 report-only、不改 exit code。
            ops.run_doctor(proc_root=self.proc)
        out = buffer.getvalue()

        self.assertIn("dream lock（runtime/locks/dream.lock）：free", out)
        self.assertIn("dream/supervise 進程：1 個（只報告，不自動 kill）", out)
        self.assertIn("pid=4242", out)
        self.assertIn("non-canonical[interpreter-mismatch,cwd-missing]", out)

    def test_doctor_reports_no_processes_when_proc_is_quiet(self):
        env = {"HIPPO_MEMORY_ROOT": str(self.memory_root),
               "PSC_MEMORY_ROOT": str(self.memory_root)}
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env), redirect_stdout(buffer):
            ops.run_doctor(proc_root=self.proc)
        out = buffer.getvalue()

        self.assertIn("dream lock（runtime/locks/dream.lock）：absent", out)
        self.assertIn("dream/supervise 進程：無", out)


if __name__ == "__main__":
    unittest.main()
