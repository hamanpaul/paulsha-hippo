"""
Test suite for processing ledger (session state machine).
"""
import json
from unittest import mock
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import processing


class TestProcessingLedger(unittest.TestCase):
    def test_append_then_fold_latest_state(self):
        """Append split then promoted for session_key `claude:s1`; state_of returns promoted."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root,
                session_key="claude:s1",
                state="split",
                now="2025-01-01T00:00:00Z",
                config_hash="hash1"
            )
            processing.append_state(
                root,
                session_key="claude:s1",
                state="promoted",
                now="2025-01-01T00:00:01Z",
                config_hash="hash1"
            )
            state = processing.state_of(root, "claude:s1")
            self.assertEqual(state, "promoted")

    def test_no_entry_means_not_processed(self):
        """No event means state_of returns None."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = processing.state_of(root, "claude:s99")
            self.assertIsNone(state)

    def test_split_state_is_in_process(self):
        """Split remains split."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root,
                session_key="claude:s2",
                state="split",
                now="2025-01-01T00:00:00Z",
                config_hash="hash1"
            )
            state = processing.state_of(root, "claude:s2")
            self.assertEqual(state, "split")

    def test_ts_uses_injected_now(self):
        """First event ts equals provided now string."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-01T12:34:56Z"
            processing.append_state(
                root,
                session_key="claude:s3",
                state="split",
                now=now_str,
                config_hash="hash1"
            )
            events = processing.read_events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["ts"], now_str)

    def test_corrupt_line_fails_closed(self):
        """If processing.jsonl contains malformed JSON line, read_events raises ProcessingLedgerError."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = processing.processing_path(root)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write valid line then corrupt line
            with open(ledger_path, "w") as f:
                f.write('{"ts":"2025-01-01T00:00:00Z","session_key":"s1","state":"split"}\n')
                f.write('this is not json\n')
            
            with self.assertRaises(processing.ProcessingLedgerError) as ctx:
                processing.read_events(root)
            
            # Error message should mention line number
            self.assertIn("line", str(ctx.exception).lower())

    def test_append_state_fsyncs_before_return(self):
        """Append forces buffered ledger data to disk before returning."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            with mock.patch("os.fsync") as fsync:
                processing.append_state(
                    root,
                    session_key="claude:s4",
                    state="split",
                    now="2025-01-01T00:00:00Z",
                    config_hash="hash1"
                )

            fsync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
