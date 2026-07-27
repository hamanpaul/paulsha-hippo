# paulsha_hippo/ledger/integrity.py
"""Append-only ledger 的位元組層完好性：撕裂行分類、原子修復、quarantine。

本模組只認識 JSONL 檔案與行，不知道 janitor / doctor / dream 的存在。
消費端自行接上（issue #64）。
"""

from __future__ import annotations

import hashlib
import json

NUL = "\x00"
QUARANTINE_SUFFIX = ".quarantine"


def line_sha256(line: str) -> str:
    """行的正規雜湊：不含結尾換行、不做 strip，寫入端與讀取端共用同一定義。"""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def classify_line(line: str) -> tuple[str, str]:
    """回傳 (classification, reason)。

    classification 為 "ok" / "recoverable" / "unrecoverable"。空白行歸為
    unrecoverable：既有 reader 也把空白行計為 bad line，若視為 ok 則
    「修復後壞行計數歸零」的不變量不成立。
    """
    stripped = line.strip().replace(NUL, "").strip()
    if not stripped:
        return ("unrecoverable", "blank")

    candidate = line.strip()
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        pass
    else:
        return ("ok", "")

    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return ("unrecoverable", "unparseable")
    return ("recoverable", "nul-torn")
