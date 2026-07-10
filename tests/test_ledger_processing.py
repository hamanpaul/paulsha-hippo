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


class TestParkedState(unittest.TestCase):
    def test_parked_is_valid_state_with_required_fields(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root,
                session_key="claude:s5",
                state="parked",
                now="2026-07-10T00:00:00Z",
                config_hash="hash1",
                failure_category="invalid_output",
                attempts=6,
                cache_key="claude:s5__" + "a" * 64,
                error="llm promote failed: no JSON array found",
            )
            self.assertEqual(processing.state_of(root, "claude:s5"), "parked")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["failure_category"], "invalid_output")
            self.assertEqual(event["attempts"], 6)
            self.assertEqual(event["cache_key"], "claude:s5__" + "a" * 64)

    def test_parked_requires_known_failure_category(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaises(ValueError):
                processing.append_state(
                    root, session_key="claude:s6", state="parked",
                    now="2026-07-10T00:00:00Z", config_hash="hash1",
                    failure_category="weird", attempts=1, cache_key="", error="x",
                )
            with self.assertRaises(ValueError):
                processing.append_state(
                    root, session_key="claude:s6", state="parked",
                    now="2026-07-10T00:00:00Z", config_hash="hash1",
                )

    def test_requeue_event_returns_session_to_split(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root, session_key="claude:s7", state="parked",
                now="2026-07-10T00:00:00Z", config_hash="hash1",
                failure_category="transient", attempts=6, cache_key="", error="t",
            )
            processing.append_state(
                root, session_key="claude:s7", state="split",
                now="2026-07-10T01:00:00Z", config_hash="hash1",
                requeued_from="parked", requeue_reason="backend fixed",
            )
            self.assertEqual(processing.state_of(root, "claude:s7"), "split")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["requeued_from"], "parked")
            self.assertEqual(event["requeue_reason"], "backend fixed")


class TestSanitizeErrorText(unittest.TestCase):
    def test_truncates_to_limit(self):
        self.assertEqual(len(processing.sanitize_error_text("x" * 2000)), 500)
        self.assertEqual(processing.sanitize_error_text("x" * 2000, limit=10), "x" * 10)

    def test_collapses_whitespace_and_masks_home(self):
        home = str(Path.home())
        raw = f"boom\n  at {home}/secret\tplace"
        out = processing.sanitize_error_text(raw)
        self.assertNotIn(home, out)
        self.assertIn("~/secret", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("\t", out)

    # #15 review F1：含 credential 的例外訊息不得原文落 ledger/evidence——
    # 必須套用 repo 既有 policy secret redaction 規則（policy/secrets.yaml）。
    _SECRET_SAMPLES = {
        "github_pat": "ghp_" + "A1b2C3d4" * 5,
        "github_fine_grained": "github_pat_" + "A1b2C3d4" * 5,
        "openai_key": "sk-" + "a1B2c3D4" * 4,
        "anthropic_key": "sk-ant-" + "a1B2c3D4" * 4,
        "aws_access_key": "AKIA" + "ABCDEFGHIJKLMNOP",
        "bearer_token": "Authorization: Bearer xyzzy-token-123456",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXBhcnQ",
    }

    def test_credentials_are_redacted_not_persisted(self):
        for name, secret in self._SECRET_SAMPLES.items():
            with self.subTest(rule=name):
                out = processing.sanitize_error_text(f"HTTP 401 calling backend: {secret} rejected")
                self.assertNotIn(secret, out)
                # bearer 樣本的 token 部分也不得殘留
                self.assertNotIn("xyzzy-token-123456", out)
                self.assertIn("REDACTED", out)

    def test_redaction_happens_before_truncation(self):
        # token 若先被截斷斬半，pattern 失配會留下敏感前綴——redaction 必須先行
        secret = "ghp_" + "Z9y8X7w6" * 5
        out = processing.sanitize_error_text(("x" * 490) + " " + secret, limit=500)
        self.assertNotIn("ghp_", out)
        self.assertLessEqual(len(out), 500)

    def test_redaction_fail_closed_returns_placeholder(self):
        # redaction 機制本身失效（policy 載入失敗）→ 整段以 placeholder 取代，
        # 原文（可能含 credential）絕不落地
        secret = "ghp_" + "A1b2C3d4" * 5
        with mock.patch(
            "paulsha_hippo.policy.load_policy",
            side_effect=RuntimeError("policy files unreadable"),
        ):
            out = processing.sanitize_error_text(f"boom {secret}")
        self.assertNotIn(secret, out)
        self.assertNotIn("boom", out)
        self.assertEqual(out, processing._REDACTION_FAILED_PLACEHOLDER)

    def test_redact_secret_text_keeps_clean_lines_in_multiline_text(self):
        secret = "sk-ant-" + "a1B2c3D4" * 4
        raw = f"line one is clean\napi_key = {secret}\nline three is clean"
        out = processing.redact_secret_text(raw)
        self.assertNotIn(secret, out)
        self.assertIn("line one is clean", out)
        self.assertIn("line three is clean", out)
        self.assertIn("REDACTED", out)


if __name__ == "__main__":
    unittest.main()
