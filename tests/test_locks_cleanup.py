"""#19 PR-C：legacy per-session lock 一次性清理（僅維護窗口；雙層安全閘）。"""
from __future__ import annotations

import fcntl
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli, ops


def _make_proc(base: Path) -> Path:
    """本檔自足的最小假 /proc（不跨測試模組 import——tests/ 非 package）。"""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1751900000\n", encoding="ascii")
    return proc


def _add_proc(proc: Path, pid: int, argv: list[str]) -> None:
    """清理閘只需要 pid + cmdline；不寫 stat/cwd（started_at 走 'unknown' fail-open）。"""
    pdir = proc / str(pid)
    pdir.mkdir()
    (pdir / "cmdline").write_bytes(b"\x00".join(a.encode("utf-8") for a in argv) + b"\x00")


class CleanupLegacyLocksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = _make_proc(self.base)  # 空 fake /proc = 維護窗口（無其他進程）
        self.memory_root = self.base / "memory"
        self.locks_dir = self.memory_root / "runtime" / "locks"
        self.locks_dir.mkdir(parents=True)
        # 現行命名（必須保留）
        (self.locks_dir / "import-ledger.lock").touch()
        (self.locks_dir / "dream.lock").touch()          # 契約 3（PR-A）
        (self.locks_dir / "lock_shard_08.lock").touch()  # 契約 4（本 PR）
        # legacy per-session 命名（清理對象）
        (self.locks_dir / "copilot-cli__sid-001.lock").touch()
        (self.locks_dir / "claude-code__abc123.lock").touch()
        # 非 .lock 檔一律不碰
        (self.locks_dir / "README.txt").touch()

    def tearDown(self):
        self.tmp.cleanup()

    def all_names(self) -> set[str]:
        return {p.name for p in self.locks_dir.iterdir()}

    def test_dry_run_lists_legacy_without_deleting(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=False,
                                          proc_root=self.proc)

        self.assertEqual(result["legacy"],
                         ["claude-code__abc123.lock", "copilot-cli__sid-001.lock"])
        self.assertEqual(result["kept"],
                         ["dream.lock", "import-ledger.lock", "lock_shard_08.lock"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["deleted"], [])
        # dry-run 完全不動檔案
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())
        self.assertIn("README.txt", self.all_names())

    def test_apply_deletes_only_legacy_and_keeps_current_names(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertTrue(result["applied"])
        self.assertEqual(result["deleted"],
                         ["claude-code__abc123.lock", "copilot-cli__sid-001.lock"])
        self.assertEqual(result["busy"], [])
        self.assertEqual(
            self.all_names(),
            {"import-ledger.lock", "dream.lock", "lock_shard_08.lock", "README.txt"})

    def test_apply_is_blocked_when_other_hippo_process_running(self):
        """安全閘第 1 層：偵測到其他 hippo 進程（舊 importer 可能執行中）→ 拒絕清理。"""
        _add_proc(
            self.proc, 5151,
            [sys.executable, "-m", "paulsha_hippo.importer.cli", "ingest",
             "--queue-item", "/q.json", "--memory-root", "/mem"])

        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        self.assertEqual([p["pid"] for p in result["other_processes"]], [5151])
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())  # 未刪任何檔

    def test_apply_skips_actively_flocked_legacy_lock(self):
        """安全閘第 2 層：逐檔 flock 探測——被持有的 legacy lock 跳過不刪（busy）。"""
        held = self.locks_dir / "copilot-cli__sid-001.lock"
        with held.open("a+", encoding="utf-8") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                              proc_root=self.proc)

        self.assertEqual(result["busy"], ["copilot-cli__sid-001.lock"])
        self.assertEqual(result["deleted"], ["claude-code__abc123.lock"])
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())

    def test_missing_locks_dir_is_a_clean_noop(self):
        empty_root = self.base / "empty-memory"
        result = ops.cleanup_legacy_locks(empty_root, apply=True, proc_root=self.proc)

        self.assertEqual(result["legacy"], [])
        self.assertEqual(result["deleted"], [])
        self.assertTrue(result["applied"])


class LocksCleanupCliTest(unittest.TestCase):
    def test_cli_dry_run_prints_json_and_exits_zero(self):
        with TemporaryDirectory() as tmp:
            memory_root = Path(tmp) / "memory"
            locks_dir = memory_root / "runtime" / "locks"
            locks_dir.mkdir(parents=True)
            (locks_dir / "copilot-cli__sid-001.lock").touch()

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                # dry-run（無 --apply）：真實 /proc 上就算有其他 hippo 進程也只列出、exit 0
                rc = cli.main(["locks", "cleanup-legacy",
                               "--memory-root", str(memory_root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["legacy"], ["copilot-cli__sid-001.lock"])
            self.assertFalse(payload["applied"])
            self.assertTrue((locks_dir / "copilot-cli__sid-001.lock").exists())


if __name__ == "__main__":
    unittest.main()
