import json
import fcntl
import tempfile
import threading
import unittest
from pathlib import Path

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.ledger import (
    LifecycleEvent,
    VALID_EVENT_TYPES,
    append_event,
    read_events,
    fold_lifecycle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class LifecycleLedgerTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)
        self.ledger_path = self.root / "lifecycle.jsonl"

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def test_valid_event_types_exist(self):
        expected = {"created", "imported", "accessed", "updated", "superseded", "archived", "restored", "deleted", "decayed", "reactivation"}
        self.assertEqual(VALID_EVENT_TYPES, expected)

    def test_append_event_creates_first_event(self):
        append_event(
            self.ledger_path,
            record_id="rec-001",
            event_type="created",
            source="test",
            reason="initial creation",
            actor="unittest"
        )
        
        events = read_events(self.ledger_path)
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["record_id"], "rec-001")
        self.assertEqual(event["event_type"], "created")
        self.assertEqual(event["source"], "test")
        self.assertEqual(event["reason"], "initial creation")
        self.assertEqual(event["actor"], "unittest")
        self.assertEqual(event["seq"], 1)
        self.assertIsNone(event["prev_hash"])
        self.assertIn("ts", event)
        self.assertIn("event_id", event)
        self.assertIn("event_hash", event)
        self.assertIsNotNone(event["event_hash"])

    def test_append_event_chains_hashes(self):
        append_event(self.ledger_path, "rec-001", "created", "test", "first", "unittest")
        append_event(self.ledger_path, "rec-001", "updated", "test", "second", "unittest")
        
        events = read_events(self.ledger_path)
        self.assertEqual(len(events), 2)
        
        self.assertEqual(events[0]["seq"], 1)
        self.assertIsNone(events[0]["prev_hash"])
        
        self.assertEqual(events[1]["seq"], 2)
        self.assertEqual(events[1]["prev_hash"], events[0]["event_hash"])

    def test_append_event_serializes_read_and_write_for_hash_chain(self):
        # Concurrent appenders must serialize their read-modify-write so seq stays
        # contiguous and prev_hash forms an unbroken chain. A real threading.Barrier
        # releases all appenders into append_event simultaneously to maximize
        # contention. Repeated over many rounds (each on a fresh ledger) so a
        # regression — e.g. releasing the lock before the write is flushed, which
        # lets a second writer read stale state and reuse a seq — is caught reliably,
        # not just probabilistically.
        thread_count = 6
        rounds = 30

        for round_index in range(rounds):
            ledger_path = self.root / f"chain-{round_index}.jsonl"
            release = threading.Barrier(thread_count)
            errors = []

            def append_record(record_id):
                try:
                    release.wait(timeout=5)
                    append_event(ledger_path, record_id, "created", "test", "concurrent", "unittest")
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [
                threading.Thread(target=append_record, args=(f"rec-{index}",))
                for index in range(thread_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(errors, [], f"round {round_index} raised: {errors}")
            events = read_events(ledger_path)
            self.assertEqual(
                sorted(event["seq"] for event in events),
                list(range(1, thread_count + 1)),
                f"round {round_index} seq not contiguous: {[e['seq'] for e in events]}",
            )
            self.assertIsNone(events[0]["prev_hash"])
            for previous, current in zip(events, events[1:]):
                self.assertEqual(
                    current["prev_hash"],
                    previous["event_hash"],
                    f"round {round_index} broken hash chain",
                )

    def test_append_event_with_metadata_and_run_id(self):
        metadata = {"key": "value", "count": 42}
        append_event(
            self.ledger_path,
            "rec-002",
            "imported",
            "importer",
            "bulk import",
            "bot",
            run_id="run-123",
            metadata=metadata
        )
        
        events = read_events(self.ledger_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["run_id"], "run-123")
        self.assertEqual(events[0]["metadata"], metadata)

    def test_read_events_missing_file(self):
        events = read_events(self.root / "nonexistent.jsonl")
        self.assertEqual(events, [])

    def test_read_events_waits_for_writer_lock(self):
        append_event(self.ledger_path, "rec-locked", "created", "test", "first", "unittest")

        with open(self.ledger_path, "r+", encoding="utf-8") as locked_file:
            fcntl.flock(locked_file.fileno(), fcntl.LOCK_EX)
            finished = threading.Event()
            errors = []

            def read_locked_events():
                try:
                    read_events(self.ledger_path)
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)
                finally:
                    finished.set()

            reader = threading.Thread(target=read_locked_events)
            reader.start()

            self.assertFalse(finished.wait(0.1))
            fcntl.flock(locked_file.fileno(), fcntl.LOCK_UN)
            reader.join(timeout=5)

        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])

    def test_fold_lifecycle_basic_transitions(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "updated"},
        ]
        
        result = fold_lifecycle(events)
        self.assertIn("rec-001", result)
        self.assertEqual(result["rec-001"]["last_state"], "active")
        self.assertEqual(result["rec-001"]["last_event_ts"], "2024-01-02T00:00:00")
        self.assertIsNone(result["rec-001"]["deleted"])

    def test_fold_lifecycle_accessed_preserves_state(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "accessed"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "active")
        self.assertEqual(result["rec-001"]["last_access_ts"], "2024-01-02T00:00:00")

    def test_fold_lifecycle_superseded(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "superseded", 
             "metadata": {"detail": {"superseded_by": "rec-002"}}},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "superseded")
        self.assertEqual(result["rec-001"]["superseded_by"], "rec-002")

    def test_fold_lifecycle_moc_reconcile_only_does_not_establish_state(self):
        # #184 review finding: an audit-only moc-reconcile dedup trace for a
        # slice with no prior lifecycle MUST NOT create effective state (it
        # previously folded to "active"). The record stays absent from the fold
        # so record_state() reports "unknown".
        events = [
            {
                "ts": "2024-01-02T00:00:00",
                "record_id": "sl-audit-only",
                "event_type": "superseded",
                "source": "moc-reconcile",
                "metadata": {
                    "deleted_path": "/tmp/older.md",
                    "kept_path": "/tmp/newer.md",
                    "schema_version": "1",
                },
            },
        ]
        result = fold_lifecycle(events)
        self.assertNotIn("sl-audit-only", result)

    def test_fold_lifecycle_moc_reconcile_superseded_preserves_active_state(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {
                "ts": "2024-01-02T00:00:00",
                "record_id": "rec-001",
                "event_type": "superseded",
                "source": "moc-reconcile",
                "metadata": {
                    "deleted_path": "/tmp/older.md",
                    "kept_path": "/tmp/newer.md",
                    "schema_version": "1",
                },
            },
        ]

        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "active")
        self.assertIsNone(result["rec-001"]["deleted"])
        self.assertEqual(result["rec-001"]["last_event_ts"], "2024-01-01T00:00:00")

    def test_fold_lifecycle_moc_reconcile_superseded_preserves_prior_inactive_state(self):
        cases = [
            (
                {"ts": "2024-01-02T00:00:00", "record_id": "rec-arch", "event_type": "archived", "reason": "cleanup"},
                "archived",
                None,
            ),
            (
                {"ts": "2024-01-02T00:00:00", "record_id": "rec-decay", "event_type": "decayed", "reason": "ttl_expired"},
                "decayed",
                None,
            ),
            (
                {"ts": "2024-01-02T00:00:00", "record_id": "rec-deleted", "event_type": "deleted"},
                "deleted",
                True,
            ),
            (
                {
                    "ts": "2024-01-02T00:00:00",
                    "record_id": "rec-superseded",
                    "event_type": "superseded",
                    "metadata": {"detail": {"superseded_by": "rec-new"}},
                },
                "superseded",
                None,
            ),
        ]

        for previous_event, expected_state, expected_deleted in cases:
            with self.subTest(record_id=previous_event["record_id"], expected_state=expected_state):
                events = [
                    {"ts": "2024-01-01T00:00:00", "record_id": previous_event["record_id"], "event_type": "created"},
                    previous_event,
                    {
                        "ts": "2024-01-03T00:00:00",
                        "record_id": previous_event["record_id"],
                        "event_type": "superseded",
                        "source": "moc-reconcile",
                        "metadata": {
                            "deleted_path": "/tmp/older.md",
                            "kept_path": "/tmp/newer.md",
                            "schema_version": "1",
                        },
                    },
                ]

                result = fold_lifecycle(events)
                self.assertEqual(result[previous_event["record_id"]]["last_state"], expected_state)
                self.assertEqual(result[previous_event["record_id"]]["deleted"], expected_deleted)
                self.assertEqual(
                    result[previous_event["record_id"]]["last_event_ts"],
                    previous_event["ts"],
                )

    def test_fold_lifecycle_archived(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "archived", "reason": "cleanup"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "archived")
        self.assertEqual(result["rec-001"]["archive_reason"], "cleanup")

    def test_fold_lifecycle_restored(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "archived", "reason": "cleanup"},
            {"ts": "2024-01-03T00:00:00", "record_id": "rec-001", "event_type": "restored"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "active")

    def test_fold_lifecycle_deleted(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "deleted"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "deleted")
        self.assertTrue(result["rec-001"]["deleted"])

    def test_fold_lifecycle_decayed(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "decayed", "reason": "ttl_expired"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "decayed")

    def test_fold_lifecycle_reactivation(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "decayed", "reason": "ttl_expired"},
            {"ts": "2024-01-03T00:00:00", "record_id": "rec-001", "event_type": "reactivation", "reason": "reimport"},
        ]
        
        result = fold_lifecycle(events)
        self.assertEqual(result["rec-001"]["last_state"], "active")
        self.assertIsNone(result["rec-001"]["deleted"])

    def test_fold_lifecycle_multiple_records(self):
        events = [
            {"ts": "2024-01-01T00:00:00", "record_id": "rec-001", "event_type": "created"},
            {"ts": "2024-01-01T00:00:01", "record_id": "rec-002", "event_type": "imported"},
            {"ts": "2024-01-02T00:00:00", "record_id": "rec-001", "event_type": "updated"},
        ]
        
        result = fold_lifecycle(events)
        self.assertIn("rec-001", result)
        self.assertIn("rec-002", result)
        self.assertEqual(result["rec-001"]["last_state"], "active")
        self.assertEqual(result["rec-002"]["last_state"], "active")


if __name__ == "__main__":
    unittest.main()
