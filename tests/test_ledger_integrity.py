# tests/test_ledger_integrity.py
import json
import hashlib
import pytest
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


def test_dry_run_does_not_touch_file(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" * 5 + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [good, torn])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = integrity.repair_file(path, apply=False, now="2026-07-27T00:00:00Z")

    assert result["recoverable"] == 1
    assert result["applied"] is False
    assert result["backup"] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not integrity.quarantine_path(path).exists()


def test_apply_removes_only_nul_and_preserves_every_other_byte(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    other = json.dumps({"n": 2}, sort_keys=True)
    torn = good[:3] + "\x00" * 577 + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [other, torn, other])

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert result["applied"] is True
    assert json.loads(lines[1]) == {"n": 1}
    assert lines[0] == other and lines[2] == other
    assert "\x00" not in path.read_text(encoding="utf-8")


def test_apply_writes_backup_identical_to_original(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    backup = Path(result["backup"])
    assert backup.exists()
    assert backup.read_bytes() == original


def test_apply_is_idempotent(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")
    after_first = path.read_bytes()
    backups_before = sorted(p.name for p in path.parent.glob("import.jsonl.bak-repair-*"))

    result = integrity.repair_file(path, apply=True, now="2026-07-27T02:00:00Z")

    assert result["recoverable"] == 0
    assert result["backup"] is None
    assert path.read_bytes() == after_first
    assert sorted(p.name for p in path.parent.glob("import.jsonl.bak-repair-*")) == backups_before


def test_apply_keeps_unrecoverable_line_in_place_and_quarantines_it(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    broken = '{"n": '
    path = _write_ledger(tmp_path, "import.jsonl", [torn, broken])

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == broken
    assert result["quarantined"] == 1
    assert integrity.read_quarantine(path) == frozenset({integrity.line_sha256(broken)})


def test_replace_failure_leaves_original_intact(tmp_path, monkeypatch):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    def boom(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(integrity.os, "replace", boom)

    with pytest.raises(OSError):
        integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    assert path.read_bytes() == original
    backups = list(path.parent.glob("import.jsonl.bak-repair-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_backup_failure_aborts_before_touching_original(tmp_path, monkeypatch):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    real_write_bytes = Path.write_bytes

    def boom(self, data):
        if ".bak-repair-" in self.name:
            raise OSError("backup failed")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", boom)

    with pytest.raises(OSError):
        integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    assert path.read_bytes() == original

# ── Task 5：janitor 讀取端排除已隔離行 ────────────────────────────────────────
from paulsha_hippo.ledger import import_log


def test_quarantined_bad_line_is_not_counted(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    broken = '{"n": '
    path = _write_ledger(tmp_path, "import.jsonl", [good, broken])
    integrity.append_quarantine(path, integrity.scan_file(path), now="2026-07-27T00:00:00Z")

    records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 0
    assert len(records) == 1


def test_unquarantined_bad_line_is_still_counted(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, '{"n": '])

    _records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 1


def test_corrupt_quarantine_list_fails_closed(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, '{"n": '])
    integrity.quarantine_path(path).write_text("這不是 JSON\n", encoding="utf-8")

    _records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 1
