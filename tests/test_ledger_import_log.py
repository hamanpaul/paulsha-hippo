"""Tests for import log reader (reactivation signals)."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from paulsha_hippo.ledger.import_log import (
    read_import_records,
    recently_imported_record_ids,
)


class TestReadImportRecords(unittest.TestCase):
    """Test read_import_records function."""

    def test_missing_log_returns_empty_list(self):
        """Missing import log should return empty list."""
        with TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "nonexistent.jsonl"
            result = read_import_records(missing_path)
            self.assertEqual(result, [])

    def test_deleted_between_check_and_read_returns_empty_list(self):
        """Import log deleted during read should honor missing-file contract."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            log_path.write_text('{"id": "rec1"}\n')

            with patch.object(Path, "read_text", side_effect=FileNotFoundError):
                result = read_import_records(log_path)

            self.assertEqual(result, [])

    def test_reads_valid_jsonl(self):
        """Should read valid JSONL records."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z", "run_id": "run1"},
                {"record_id": "rec2", "ts": "2024-01-01T11:00:00Z", "run_id": "run1"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = read_import_records(log_path)
            self.assertEqual(result, records)

    def test_empty_log_returns_empty_list(self):
        """Empty log file should return empty list."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            log_path.write_text("")
            
            result = read_import_records(log_path)
            self.assertEqual(result, [])

    def test_invalid_json_raises_valueerror_with_line_number(self):
        """Invalid JSON should raise ValueError with line number context."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            log_path.write_text('{"id": "rec1"}\n{bad json}\n{"id": "rec3"}\n')
            
            with self.assertRaises(ValueError) as ctx:
                read_import_records(log_path)
            
            # Should mention line number in error message
            error_msg = str(ctx.exception)
            self.assertIn("line", error_msg.lower())
            self.assertIn("2", error_msg)  # Line 2 has the bad JSON


class TestRecentlyImportedRecordIds(unittest.TestCase):
    """Test recently_imported_record_ids function."""

    def test_missing_log_returns_empty_set(self):
        """Missing import log should return empty set."""
        with TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "nonexistent.jsonl"
            result = recently_imported_record_ids(missing_path)
            self.assertEqual(result, set())

    def test_extracts_record_id_field(self):
        """Should extract IDs from record_id field."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": "rec1", "ts": "2024-01-01T10:00:00Z"},
                {"record_id": "rec2", "ts": "2024-01-01T11:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, {"rec1", "rec2"})

    def test_extracts_id_field(self):
        """Should extract IDs from id field."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z"},
                {"id": "rec2", "ts": "2024-01-01T11:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, {"rec1", "rec2"})

    def test_ignores_rows_without_ids(self):
        """Should ignore rows without record_id or id."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z"},
                {"ts": "2024-01-01T11:00:00Z"},  # No ID field
                {"record_id": "rec2", "ts": "2024-01-01T12:00:00Z"},
                {"other_field": "value"},  # No ID field
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, {"rec1", "rec2"})

    def test_filters_by_run_id(self):
        """Should filter records by run_id when provided."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z", "run_id": "run1"},
                {"id": "rec2", "ts": "2024-01-01T11:00:00Z", "run_id": "run2"},
                {"id": "rec3", "ts": "2024-01-01T12:00:00Z", "run_id": "run1"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path, run_id="run1")
            self.assertEqual(result, {"rec1", "rec3"})

    def test_filters_by_since_ts(self):
        """Should filter records by since_ts using ISO timestamp ordering."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z"},
                {"id": "rec2", "ts": "2024-01-01T11:00:00Z"},
                {"id": "rec3", "ts": "2024-01-01T12:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path, since_ts="2024-01-01T11:00:00Z")
            self.assertEqual(result, {"rec2", "rec3"})

    def test_since_ts_filter_handles_mixed_precision_timestamps(self):
        """Timestamp filtering should compare chronological instants, not strings."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "after", "ts": "2026-03-01T00:00:00.123456Z"},
                {"id": "same", "ts": "2026-03-01T00:00:00+00:00"},
                {"id": "before", "ts": "2026-02-28T23:59:59.999999Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

            result = recently_imported_record_ids(log_path, since_ts="2026-03-01T00:00:00Z")
            self.assertEqual(result, {"after", "same"})

    def test_malformed_since_ts_raises_value_error(self):
        """Malformed since_ts should fail explicitly instead of filtering everything."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            log_path.write_text(json.dumps({"id": "rec1", "ts": "2024-01-01T10:00:00Z"}) + "\n")

            with self.assertRaises(ValueError):
                recently_imported_record_ids(log_path, since_ts="2024-01-01T10:00:00UTC")

    def test_non_string_since_ts_raises_value_error(self):
        """Non-string since_ts should fail explicitly."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            log_path.write_text(json.dumps({"id": "rec1", "ts": "2024-01-01T10:00:00Z"}) + "\n")

            with self.assertRaises(ValueError):
                recently_imported_record_ids(log_path, since_ts=123)  # type: ignore[arg-type]

    def test_since_ts_filter_skips_non_string_timestamps(self):
        """Malformed non-string timestamps should not crash since_ts filtering."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": "bad", "ts": 123456789},
                {"record_id": "good", "ts": "2024-01-01T12:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

            result = recently_imported_record_ids(log_path, since_ts="2024-01-01T10:00:00Z")
            self.assertEqual(result, {"good"})

    def test_filters_by_both_run_id_and_since_ts(self):
        """Should apply both run_id and since_ts filters."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"id": "rec1", "ts": "2024-01-01T10:00:00Z", "run_id": "run1"},
                {"id": "rec2", "ts": "2024-01-01T11:00:00Z", "run_id": "run2"},
                {"id": "rec3", "ts": "2024-01-01T12:00:00Z", "run_id": "run1"},
                {"id": "rec4", "ts": "2024-01-01T13:00:00Z", "run_id": "run2"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(
                log_path, run_id="run1", since_ts="2024-01-01T11:00:00Z"
            )
            self.assertEqual(result, {"rec3"})

    def test_prefers_record_id_over_id(self):
        """When both record_id and id present, should prefer record_id."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": "rec1", "id": "other1", "ts": "2024-01-01T10:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            
            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, {"rec1"})

    def test_converts_non_string_ids_to_strings(self):
        """Extracted IDs should honor the set[str] return contract."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": 123, "ts": "2024-01-01T10:00:00Z"},
                {"id": 456, "ts": "2024-01-01T11:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, {"123", "456"})

    def test_empty_record_id_does_not_fallback_to_id(self):
        """Empty record_id is not a usable ID and should not fallback to id."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": "", "id": "fallback", "ts": "2024-01-01T10:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, set())

    def test_whitespace_only_id_is_ignored(self):
        """Whitespace-only IDs are not meaningful import record IDs."""
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "import.jsonl"
            records = [
                {"record_id": "   ", "ts": "2024-01-01T10:00:00Z"},
                {"id": "\t", "ts": "2024-01-01T11:00:00Z"},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

            result = recently_imported_record_ids(log_path)
            self.assertEqual(result, set())


if __name__ == "__main__":
    unittest.main()
