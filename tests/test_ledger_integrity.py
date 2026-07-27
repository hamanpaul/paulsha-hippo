# tests/test_ledger_integrity.py
import json

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
