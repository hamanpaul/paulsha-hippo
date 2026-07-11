"""#19 PR-C：cleanup-legacy 安全閘回歸（Codex blocking findings）。

三條 high-severity：
  1. symlinked locks 目錄逃逸刪除 memory_root 外部檔案（路徑閘）
  2. /proc 掃描失敗時 fail-open 誤判維護窗口已淨空（進程閘 scan_ok）
  3. 未知命名 .lock 被 denylist 誤刪（名稱閘：正向辨識 + unknown 保留＋阻擋）
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import ops


def _empty_fake_proc(base: Path) -> Path:
    """空 fake /proc（可列舉、無其他 hippo 進程）= 已確認淨空的維護窗口。"""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1751900000\n", encoding="ascii")
    return proc


class SymlinkedLocksDirTest(unittest.TestCase):
    """路徑閘：locks 為 symlink → 拒絕，且不刪 memory_root 外部檔案。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = _empty_fake_proc(self.base)
        self.memory_root = self.base / "memory"

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_refuses_symlinked_locks_dir_and_spares_external_files(self):
        external = self.base / "external-locks"
        external.mkdir()
        victim = external / "copilot-cli__external-sid.lock"  # legacy 命名的外部檔
        victim.touch()
        runtime = self.memory_root / "runtime"
        runtime.mkdir(parents=True)
        os.symlink(external, runtime / "locks")  # locks -> 外部目錄

        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertTrue(result.get("unsafe_locks_dir"))
        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        self.assertEqual(result["legacy"], [])  # 未經 symlink 列舉任何檔
        self.assertEqual(result["deleted"], [])
        self.assertTrue(victim.exists())  # 外部檔案完好無損（未逃逸刪除）

    def test_symlink_entry_masquerading_as_legacy_lock_is_unknown_not_deleted(self):
        locks_dir = self.memory_root / "runtime" / "locks"
        locks_dir.mkdir(parents=True)
        external = self.base / "outside.lock"
        external.touch()
        # 一個 legacy 命名的 symlink 指向外部真檔——不得跟隨、不得刪
        os.symlink(external, locks_dir / "copilot-cli__x.lock")

        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertIn("copilot-cli__x.lock", result["unknown"])
        self.assertNotIn("copilot-cli__x.lock", result["legacy"])
        self.assertIn("blocked", result)  # unknown → 阻擋 --apply
        self.assertFalse(result["applied"])
        self.assertTrue(external.exists())  # 外部真檔完好
        self.assertTrue((locks_dir / "copilot-cli__x.lock").is_symlink())  # symlink 仍在


class ProcScanFailClosedTest(unittest.TestCase):
    """進程閘：/proc 掃描失敗必須 fail-closed（不得把空清單當已淨空）。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.locks_dir = self.base / "memory" / "runtime" / "locks"
        self.locks_dir.mkdir(parents=True)
        (self.locks_dir / "copilot-cli__sid-001.lock").touch()

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_blocked_when_proc_root_missing(self):
        missing_proc = self.base / "no-such-proc"  # 不存在 → listdir 失敗

        result = ops.cleanup_legacy_locks(self.base / "memory", apply=True,
                                          proc_root=missing_proc)

        self.assertFalse(result["scan_ok"])
        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        self.assertEqual(result["deleted"], [])
        self.assertTrue((self.locks_dir / "copilot-cli__sid-001.lock").exists())

    def test_apply_blocked_when_proc_root_is_a_file(self):
        not_a_dir = self.base / "proc-file"  # 非目錄 → listdir 失敗（非 Linux 情境代理）
        not_a_dir.write_text("x", encoding="ascii")

        result = ops.cleanup_legacy_locks(self.base / "memory", apply=True,
                                          proc_root=not_a_dir)

        self.assertFalse(result["scan_ok"])
        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        self.assertTrue((self.locks_dir / "copilot-cli__sid-001.lock").exists())


class UnknownLockNamePreservedTest(unittest.TestCase):
    """名稱閘：正向辨識 legacy，未知命名一律保留＋阻擋（denylist 誤刪回歸）。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = _empty_fake_proc(self.base)
        self.memory_root = self.base / "memory"
        self.locks_dir = self.memory_root / "runtime" / "locks"
        self.locks_dir.mkdir(parents=True)
        (self.locks_dir / "copilot-cli__sid-001.lock").touch()   # legacy（正向辨識）
        (self.locks_dir / "claude-code__abc.lock").touch()       # legacy
        (self.locks_dir / "future-global-writer.lock").touch()   # 未知（不得誤刪）
        (self.locks_dir / "lock_shard_00.lock").touch()          # 現行 shard（kept）
        (self.locks_dir / "import-ledger.lock").touch()          # 現行共享鎖（kept）

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_name_is_classified_unknown_and_blocks_apply(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertEqual(result["unknown"], ["future-global-writer.lock"])
        self.assertEqual(result["legacy"],
                         ["claude-code__abc.lock", "copilot-cli__sid-001.lock"])
        self.assertIn("lock_shard_00.lock", result["kept"])
        self.assertIn("import-ledger.lock", result["kept"])
        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        # 阻擋後一檔不刪（含被正確辨識的 legacy）
        for name in ("future-global-writer.lock", "copilot-cli__sid-001.lock",
                     "claude-code__abc.lock"):
            self.assertTrue((self.locks_dir / name).exists(), f"{name} 不應被刪")

    def test_dry_run_lists_unknown_bucket_without_deleting(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=False,
                                          proc_root=self.proc)

        self.assertEqual(result["unknown"], ["future-global-writer.lock"])
        self.assertFalse(result["applied"])
        self.assertNotIn("blocked", result)  # dry-run 不設 blocked
        self.assertTrue((self.locks_dir / "future-global-writer.lock").exists())

    def test_legacy_prefixes_are_derived_from_idempotency_key_formula(self):
        """前綴由 safe_key 反推、非字面字串——契約未漂移。"""
        from paulsha_hippo.importer.pipeline import idempotency_key, safe_key

        # 模擬歷史 per-session lock：safe_key(idempotency_key(session)) + ".lock"
        session = {"tool": "codex", "session_id": "run/2024:xyz"}
        legacy_name = safe_key(idempotency_key(session)) + ".lock"
        prefixes = ops._legacy_lock_prefixes()

        self.assertTrue(ops._is_legacy_session_lock_name(legacy_name, prefixes))
        self.assertFalse(
            ops._is_legacy_session_lock_name("future-global-writer.lock", prefixes))


if __name__ == "__main__":
    unittest.main()
