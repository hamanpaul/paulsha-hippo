# paulsha_hippo/ledger/integrity.py
"""Append-only ledger 的位元組層完好性：撕裂行分類、原子修復、quarantine。

本模組只認識 JSONL 檔案與行，不知道 janitor / doctor / dream 的存在。
消費端自行接上（issue #64）。
"""

from __future__ import annotations

import hashlib
import json
import os
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


def quarantine_path(ledger_path: Path) -> Path:
    return ledger_path.with_name(ledger_path.name + QUARANTINE_SUFFIX)


def read_quarantine(ledger_path: Path) -> frozenset[str] | None:
    """已隔離行的 sha256 集合。

    清單不存在 → 空集合。清單存在但任一行不可解析 → None，呼叫端必須
    fail-closed（壞行一律照計），不得因清單不可讀而放行。
    """
    path = quarantine_path(ledger_path)
    if not path.is_file():
        return frozenset()
    hashes: set[str] = set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        digest = record.get("sha256")
        if not isinstance(digest, str) or not digest:
            return None
        hashes.add(digest)
    return frozenset(hashes)


def append_quarantine(ledger_path: Path, findings: list[LineFinding], *, now: str) -> int:
    """把不可救回行記入 quarantine 清單，回傳實際新增筆數（已存在者不重複記）。"""
    existing = read_quarantine(ledger_path)
    if existing is None:
        raise ValueError(f"quarantine 清單不可解析：{quarantine_path(ledger_path)}")
    rows = [f for f in findings if f.classification == "unrecoverable" and f.sha256 not in existing]
    if not rows:
        return 0
    path = quarantine_path(ledger_path)
    with path.open("a", encoding="utf-8") as handle:
        for finding in rows:
            handle.write(
                json.dumps(
                    {
                        "line_no": finding.line_no,
                        "sha256": finding.sha256,
                        "reason": finding.reason,
                        "quarantined_at": now,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    return len(rows)


def _backup_name(path: Path, now: str) -> str:
    stamp = now.replace(":", "").replace("-", "")
    return f"{path.name}.bak-repair-{stamp}"


def repair_file(path: Path, *, apply: bool, now: str) -> dict:
    findings = scan_file(path)
    recoverable = [f for f in findings if f.classification == "recoverable"]
    unrecoverable = [f for f in findings if f.classification == "unrecoverable"]
    result: dict = {
        "file": str(path),
        "recoverable": len(recoverable),
        "unrecoverable": len(unrecoverable),
        "quarantined": 0,
        "backup": None,
        "applied": False,
    }
    if not apply:
        return result

    if unrecoverable:
        result["quarantined"] = append_quarantine(path, unrecoverable, now=now)

    if not recoverable:
        # 冪等：沒有可救回行就不寫檔、不備份。
        return result

    targets = {f.line_no for f in recoverable}
    lines = _read_lines(path)
    rebuilt = [
        line.replace(NUL, "") if index in targets else line
        for index, line in enumerate(lines, start=1)
    ]

    backup = path.with_name(_backup_name(path, now))
    backup.write_bytes(path.read_bytes())

    tmp = path.with_name(f".{path.name}.repair.tmp")
    with tmp.open("w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write("\n".join(rebuilt) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

    result["backup"] = str(backup)
    result["applied"] = True
    return result


def repair_ledger_dir(memory_root: Path, *, apply: bool, now: str) -> list[dict]:
    directory = ledger_dir(memory_root)
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.glob("*.jsonl")):
        outcome = repair_file(path, apply=apply, now=now)
        if outcome["recoverable"] or outcome["unrecoverable"]:
            results.append(outcome)
    return results
