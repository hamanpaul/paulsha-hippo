"""
Test suite for processing ledger (session state machine).
"""
import json
import os
from contextlib import contextmanager
from unittest import mock
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import processing


@contextmanager
def _global_disable_rules_override(rule_ids):
    """模擬使用者 policy.override.yaml 的全域 disable_rules 生效（Codex 複驗情境）。

    HOME 與 PSC_CONFIG_ROOT 同時導向暫存 config，確保無論環境變數解析路徑為何，
    無參數 load_policy() 都會讀到這份 override。
    """
    with TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / ".config" / "paulshaclaw"
        config_dir.mkdir(parents=True)
        (config_dir / "policy.override.yaml").write_text(
            json.dumps({"disable_rules": list(rule_ids)}), encoding="utf-8"
        )
        with mock.patch.dict(
            os.environ, {"HOME": str(tmp), "PSC_CONFIG_ROOT": str(config_dir)}
        ):
            yield


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


class TestTransitionStateAtomic(unittest.TestCase):
    """條件式原子轉移：整段 read-check-write 持同一把 exclusive lock。

    守門一律以「持鎖重讀後 fold」為準，不信任呼叫端快照——攔下 parked→split 的
    read-check-write race（快照後被 promote/park 的錯誤復活、較舊 ts 的 false-success）。
    """

    def _park(self, root: Path, key: str, *, now: str) -> None:
        processing.append_state(
            root, session_key=key, state="parked", now=now, config_hash="cfg",
            failure_category="transient", attempts=6, cache_key="", error="boom",
        )

    def test_transition_appends_when_current_state_expected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._park(root, "claude:s1", now="2026-07-10T00:00:00Z")
            ok, reason = processing.transition_state_atomic(
                root, session_key="claude:s1", expected_states=("parked",),
                state="split", now="2026-07-10T01:00:00Z", config_hash="cfg",
                requeued_from="parked", requeue_reason="fixed",
            )
            self.assertEqual((ok, reason), (True, ""))
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["state"], "split")
            self.assertEqual(event["requeued_from"], "parked")
            self.assertEqual(event["requeue_reason"], "fixed")

    def test_transition_equal_timestamp_wins_via_append_order(self):
        # now == 最新事件 ts：後追加者（index 較大）在 fold 的 (ts, index) 排序中勝出，
        # 屬合法轉移；只有『嚴格較舊』才是 stale。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._park(root, "claude:s1", now="2026-07-10T00:00:00Z")
            ok, reason = processing.transition_state_atomic(
                root, session_key="claude:s1", expected_states=("parked",),
                state="split", now="2026-07-10T00:00:00Z", config_hash="cfg",
            )
            self.assertEqual((ok, reason), (True, ""))
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")

    def test_transition_refuses_when_state_changed_after_snapshot(self):
        # 併發 writer 在（呼叫端）快照後已 promote：轉移必須以持鎖重讀為準，
        # 拒絕而非把已 promote 的 session 錯誤復活成 split。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._park(root, "claude:s1", now="2026-07-10T00:00:00Z")
            processing.append_state(
                root, session_key="claude:s1", state="promoted",
                now="2026-07-10T00:30:00Z", config_hash="cfg",
            )
            ok, reason = processing.transition_state_atomic(
                root, session_key="claude:s1", expected_states=("parked",),
                state="split", now="2026-07-10T01:00:00Z", config_hash="cfg",
            )
            self.assertEqual((ok, reason), (False, "promoted"))
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            # 拒絕分支不得寫入任何 split 事件
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_transition_refuses_stale_timestamp(self):
        # 較舊 `now`：即便目前狀態仍是 parked，較舊 ts 不會贏得 ts 排序的 fold，
        # append 會被既有 parked 事件遮蔽、狀態實際不變——必須拒絕（否則 false-success）。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._park(root, "claude:s1", now="2026-07-10T00:00:00Z")
            ok, reason = processing.transition_state_atomic(
                root, session_key="claude:s1", expected_states=("parked",),
                state="split", now="2026-07-09T00:00:00Z", config_hash="cfg",
            )
            self.assertEqual((ok, reason), (False, "stale-timestamp"))
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            # 拒絕在寫入之前：ledger 不得出現 split 事件
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_transition_refuses_unknown_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ok, reason = processing.transition_state_atomic(
                root, session_key="claude:ghost", expected_states=("parked",),
                state="split", now="2026-07-10T01:00:00Z", config_hash="cfg",
            )
            self.assertEqual((ok, reason), (False, "unknown session"))
            self.assertEqual(processing.read_events(root), [])

    def test_transition_validates_target_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                processing.transition_state_atomic(
                    root, session_key="claude:s1", expected_states=("parked",),
                    state="bogus", now="2026-07-10T01:00:00Z", config_hash="cfg",
                )
            with self.assertRaises(ValueError):
                # parked target 需已知 failure_category（比照 append_state）
                processing.transition_state_atomic(
                    root, session_key="claude:s1", expected_states=("split",),
                    state="parked", now="2026-07-10T01:00:00Z", config_hash="cfg",
                )


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

    # Codex 複驗 blocking：policy.override.yaml 的 disable_rules 是正式功能（使用者
    # 可停用誤判規則），但持久化出口的強制 scrub 必須用不可被 override 停用的
    # baseline 規則——否則 disable_rules 一設，credential 原文直落 _failed/*.json、
    # processing.jsonl、dream.jsonl。
    def test_global_disable_rules_override_cannot_weaken_sanitize(self):
        from paulsha_hippo.policy import load_default_policy, load_policy

        all_rule_ids = sorted(load_default_policy().secret_rules)
        with _global_disable_rules_override(all_rule_ids):
            # 情境武裝確認：一般（可 override）load_policy 確實讀到全域停用
            self.assertEqual(set(load_policy().disabled_rules), set(all_rule_ids))
            for name, secret in self._SECRET_SAMPLES.items():
                with self.subTest(rule=name):
                    out = processing.sanitize_error_text(f"HTTP 401: {secret} rejected")
                    self.assertNotIn(secret, out)
                    self.assertNotIn("xyzzy-token-123456", out)
                    self.assertIn("REDACTED", out)

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
