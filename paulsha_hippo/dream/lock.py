"""Global dream singleton lock（#19／#15 失敗鏈——單一 dream writer）。

固定路徑 <memory_root>/runtime/locks/dream.lock（跨批次共享契約：PR-C doctor
引用同一路徑報告持鎖狀態）。dream run 入口以 flock(LOCK_EX|LOCK_NB) 整輪持有；
lock 檔是 flock rendezvous inode，永不 unlink（unlink 會破壞互斥）。
"""
from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


def dream_lock_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "locks" / "dream.lock"


def acquire_dream_lock(memory_root: Path) -> IO[str] | None:
    """Non-blocking 全域 dream lock。

    成功回傳持鎖 handle（caller close() 即釋放）；已被他人持有回傳 None。
    """
    path = dream_lock_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
