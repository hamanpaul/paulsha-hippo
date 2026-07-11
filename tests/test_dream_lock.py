from __future__ import annotations

import errno
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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

    # review F4：只有 contention（鎖被他人持有）可回 None；其餘 OSError 必須上拋，
    # 不得偽裝成「another process」讓排程永遠假成功、backlog 永不處理。
    def test_contention_errnos_return_none(self):
        for err in (errno.EAGAIN, errno.EACCES):
            with self.subTest(errno=err), TemporaryDirectory() as tmp:
                with mock.patch.object(lock.fcntl, "flock",
                                       side_effect=OSError(err, "lock held")):
                    self.assertIsNone(lock.acquire_dream_lock(Path(tmp)))

    def test_blocking_io_error_returns_none(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(lock.fcntl, "flock",
                                   side_effect=BlockingIOError(errno.EAGAIN, "again")):
                self.assertIsNone(lock.acquire_dream_lock(Path(tmp)))

    def test_non_contention_oserror_propagates(self):
        for err in (errno.ENOLCK, errno.EIO):
            with self.subTest(errno=err), TemporaryDirectory() as tmp:
                with mock.patch.object(lock.fcntl, "flock",
                                       side_effect=OSError(err, "flock unsupported")):
                    with self.assertRaises(OSError) as ctx:
                        lock.acquire_dream_lock(Path(tmp))
                    self.assertEqual(ctx.exception.errno, err)


if __name__ == "__main__":
    unittest.main()
