"""
Tests for ledger retrieval set API.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import append_event, active_record_ids


class TestActiveRecordIds(unittest.TestCase):
    """Test active_record_ids retrieval set function."""

    def setUp(self):
        """Create temporary test directory."""
        self.temp_dir = TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.lifecycle_path = self.test_dir / "lifecycle.jsonl"

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_missing_lifecycle_file_returns_empty_set(self):
        """Missing lifecycle file should return empty set."""
        non_existent = self.test_dir / "nonexistent.jsonl"
        result = active_record_ids(non_existent)
        self.assertEqual(result, set())

    def test_created_record_is_active(self):
        """Created record should be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec1",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec1"})

    def test_imported_record_is_active(self):
        """Imported record should be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec2",
            event_type="imported",
            source="test",
            reason="test import",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec2"})

    def test_updated_record_remains_active(self):
        """Updated record should remain in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec3",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec3",
            event_type="updated",
            source="test",
            reason="test update",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec3"})

    def test_restored_record_becomes_active(self):
        """Restored record should be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec4",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec4",
            event_type="archived",
            source="test",
            reason="test archive",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec4",
            event_type="restored",
            source="test",
            reason="test restore",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec4"})

    def test_restored_deleted_record_becomes_active(self):
        """Restored event should reactivate a previously deleted record."""
        append_event(
            self.lifecycle_path,
            record_id="rec4b",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec4b",
            event_type="deleted",
            source="test",
            reason="test deletion",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec4b",
            event_type="restored",
            source="test",
            reason="test restore",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec4b"})

    def test_archived_record_excluded(self):
        """Archived record should not be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec5",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec5",
            event_type="archived",
            source="test",
            reason="test archive",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, set())

    def test_superseded_record_excluded(self):
        """Superseded record should not be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec6",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec6",
            event_type="superseded",
            source="test",
            reason="superseded by newer",
            actor="test_user",
            metadata={"detail": {"superseded_by": "rec7"}},
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, set())

    def test_deleted_record_excluded(self):
        """Deleted record should not be in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec8",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec8",
            event_type="deleted",
            source="test",
            reason="test deletion",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, set())

    def test_accessed_preserves_active_state(self):
        """Accessed event should preserve record in active set."""
        append_event(
            self.lifecycle_path,
            record_id="rec9",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec9",
            event_type="accessed",
            source="test",
            reason="test access",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"rec9"})

    def test_accessed_only_record_is_not_active(self):
        """Accessed event without prior lifecycle creation should not activate a record."""
        append_event(
            self.lifecycle_path,
            record_id="rec9b",
            event_type="accessed",
            source="test",
            reason="test access",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, set())

    def test_accessed_does_not_restore_archived(self):
        """Accessed event should not restore archived record."""
        append_event(
            self.lifecycle_path,
            record_id="rec10",
            event_type="created",
            source="test",
            reason="test creation",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec10",
            event_type="archived",
            source="test",
            reason="test archive",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="rec10",
            event_type="accessed",
            source="test",
            reason="test access",
            actor="test_user",
        )
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, set())

    def test_mixed_active_and_inactive_records(self):
        """Mixed set should return only active records."""
        # Active records
        append_event(
            self.lifecycle_path,
            record_id="active1",
            event_type="created",
            source="test",
            reason="test",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="active2",
            event_type="imported",
            source="test",
            reason="test",
            actor="test_user",
        )
        
        # Archived record
        append_event(
            self.lifecycle_path,
            record_id="archived1",
            event_type="created",
            source="test",
            reason="test",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="archived1",
            event_type="archived",
            source="test",
            reason="test",
            actor="test_user",
        )
        
        # Deleted record
        append_event(
            self.lifecycle_path,
            record_id="deleted1",
            event_type="created",
            source="test",
            reason="test",
            actor="test_user",
        )
        append_event(
            self.lifecycle_path,
            record_id="deleted1",
            event_type="deleted",
            source="test",
            reason="test",
            actor="test_user",
        )
        
        result = active_record_ids(self.lifecycle_path)
        self.assertEqual(result, {"active1", "active2"})


if __name__ == "__main__":
    unittest.main()
