from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.dream import lock


class DreamLockTests(unittest.TestCase):
    def test_lock_path_is_fixed_contract_path(self):
        root = Path("/tmp-any")
        self.assertEqual(
            lock.dream_lock_path(root), root / "runtime" / "locks" / "dream.lock"
        )

    def test_acquire_creates_lock_file_and_returns_handle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle = lock.acquire_dream_lock(root)
            self.assertIsNotNone(handle)
            self.assertTrue(lock.dream_lock_path(root).exists())
            handle.close()

    def test_second_acquire_fails_while_held_then_succeeds_after_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = lock.acquire_dream_lock(root)
            self.assertIsNotNone(first)
            self.assertIsNone(lock.acquire_dream_lock(root))
            first.close()
            second = lock.acquire_dream_lock(root)
            self.assertIsNotNone(second)
            second.close()

    def test_lock_file_is_never_unlinked_on_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle = lock.acquire_dream_lock(root)
            handle.close()
            # flock rendezvous inode 永不 unlink（#19：執行中 unlink 會破壞互斥）
            self.assertTrue(lock.dream_lock_path(root).exists())


if __name__ == "__main__":
    unittest.main()
