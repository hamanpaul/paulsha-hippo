# paulsha_hippo/ledger/integrity.py
"""Append-only ledger 的位元組層完好性：撕裂行分類、原子修復、quarantine。

本模組只認識 JSONL 檔案與行，不知道 janitor / doctor / dream 的存在。
消費端自行接上（issue #64）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class LineFinding:
    line_no: int
    sha256: str
    classification: str
    reason: str


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()


def scan_file(path: Path) -> list[LineFinding]:
    """唯讀掃描單一 JSONL，只回傳非 ok 的行。行號為 1-indexed。"""
    if not path.is_file():
        return []
    findings: list[LineFinding] = []
    for index, line in enumerate(_read_lines(path), start=1):
        classification, reason = classify_line(line)
        if classification == "ok":
            continue
        findings.append(
            LineFinding(
                line_no=index,
                sha256=line_sha256(line),
                classification=classification,
                reason=reason,
            )
        )
    return findings


def ledger_dir(memory_root: Path) -> Path:
    return memory_root / "runtime" / "ledger"


def scan_ledger_dir(memory_root: Path) -> dict[str, list[LineFinding]]:
    """掃描 runtime/ledger/*.jsonl，只回傳有壞行的檔（key 為檔名）。"""
    directory = ledger_dir(memory_root)
    if not directory.is_dir():
        return {}
    result: dict[str, list[LineFinding]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        findings = scan_file(path)
        if findings:
            result[path.name] = findings
    return result
