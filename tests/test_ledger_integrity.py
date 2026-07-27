# tests/test_ledger_integrity.py
import json
import hashlib
from pathlib import Path

from paulsha_hippo.ledger import integrity


def test_classify_ok_line():
    line = json.dumps({"status": "written"}, sort_keys=True)
    assert integrity.classify_line(line) == ("ok", "")


def test_classify_nul_torn_line_is_recoverable():
    payload = json.dumps({"status": "written", "id": "abc"}, sort_keys=True)
    torn = payload[:10] + "\x00" * 577 + payload[10:]
    assert integrity.classify_line(torn) == ("recoverable", "nul-torn")


def test_classify_blank_line_is_unrecoverable_blank():
    assert integrity.classify_line("") == ("unrecoverable", "blank")
    assert integrity.classify_line("   ") == ("unrecoverable", "blank")


def test_classify_truncated_json_is_unrecoverable():
    assert integrity.classify_line('{"status": "writ') == ("unrecoverable", "unparseable")


def test_classify_all_nul_line_is_unrecoverable():
    assert integrity.classify_line("\x00" * 32) == ("unrecoverable", "blank")


def test_line_sha256_is_over_raw_line_without_strip():
    import hashlib

    line = '  {"a": 1}  '
    assert integrity.line_sha256(line) == hashlib.sha256(line.encode("utf-8")).hexdigest()


def _write_ledger(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / "runtime" / "ledger" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_scan_file_reports_line_numbers_and_classification(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:4] + "\x00" * 8 + good[4:]
    path = _write_ledger(tmp_path, "import.jsonl", [good, torn, '{"n": '])

    findings = integrity.scan_file(path)

    assert [f.line_no for f in findings] == [2, 3]
    assert findings[0].classification == "recoverable"
    assert findings[0].reason == "nul-torn"
    assert findings[0].sha256 == integrity.line_sha256(torn)
    assert findings[1].classification == "unrecoverable"
    assert findings[1].reason == "unparseable"


def test_scan_file_on_clean_ledger_returns_empty(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", [json.dumps({"n": 1})])
    assert integrity.scan_file(path) == []


def test_scan_file_does_not_modify_the_file(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, good[:3] + "\x00" + good[3:]])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    integrity.scan_file(path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_scan_ledger_dir_skips_clean_files_and_missing_dir(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    _write_ledger(tmp_path, "clean.jsonl", [good])
    _write_ledger(tmp_path, "import.jsonl", [good[:3] + "\x00" + good[3:]])

    result = integrity.scan_ledger_dir(tmp_path)

    assert set(result) == {"import.jsonl"}
    assert integrity.scan_ledger_dir(tmp_path / "nope") == {}


def test_scan_ledger_dir_ignores_quarantine_sidecars(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    _write_ledger(tmp_path, "import.jsonl", [good])
    _write_ledger(tmp_path, "import.jsonl.quarantine", ["not json at all"])

    assert integrity.scan_ledger_dir(tmp_path) == {}


def test_read_quarantine_missing_returns_empty(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", [json.dumps({"n": 1})])
    assert integrity.read_quarantine(path) == frozenset()


def test_append_then_read_quarantine_roundtrip(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    findings = integrity.scan_file(path)

    written = integrity.append_quarantine(path, findings, now="2026-07-27T00:00:00Z")

    assert written == 1
    assert integrity.read_quarantine(path) == frozenset({findings[0].sha256})
    record = json.loads(integrity.quarantine_path(path).read_text().splitlines()[0])
    assert record["line_no"] == 1
    assert record["sha256"] == findings[0].sha256
    assert record["reason"] == "unparseable"
    assert record["quarantined_at"] == "2026-07-27T00:00:00Z"


def test_append_quarantine_is_idempotent(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    findings = integrity.scan_file(path)
    integrity.append_quarantine(path, findings, now="2026-07-27T00:00:00Z")

    written = integrity.append_quarantine(path, findings, now="2026-07-27T01:00:00Z")

    assert written == 0
    assert len(integrity.quarantine_path(path).read_text().splitlines()) == 1


def test_read_quarantine_corrupt_returns_none_fail_closed(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    integrity.quarantine_path(path).write_text("這不是 JSON\n", encoding="utf-8")

    assert integrity.read_quarantine(path) is None
