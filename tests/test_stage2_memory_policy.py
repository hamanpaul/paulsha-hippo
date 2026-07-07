from __future__ import annotations

import importlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


TEMP_ROOT = Path(__file__).resolve().parent


def temporary_directory():
    return TemporaryDirectory(dir=TEMP_ROOT)


def load_policy(testcase: unittest.TestCase):
    try:
        return importlib.import_module("paulsha_hippo.policy")
    except ModuleNotFoundError as exc:
        testcase.fail(f"memory policy module missing: {exc}")


class PolicyLoaderTests(unittest.TestCase):
    def test_load_defaults_lists_required_boundaries_and_rules(self):
        mod = load_policy(self)
        policy = mod.load_default_policy()
        self.assertEqual(policy.policy_version, "0.1.0")
        self.assertIn("external_to_raw", policy.boundaries)
        self.assertIn("raw_to_distilled", policy.boundaries)
        self.assertIn("github_pat", policy.secret_rules)
        self.assertEqual(policy.classification.unknown_project_default, "private")

    def test_local_override_changes_effective_hash_and_disables_session_rule(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text('{"disable_rules_for_session": {"session-a": ["github_pat"]}}', encoding="utf-8")
            base = mod.load_policy(override_path=None)
            overridden = mod.load_policy(override_path=override)
        self.assertNotEqual(base.effective_policy_hash, overridden.effective_policy_hash)
        self.assertIn("github_pat", overridden.disabled_rules_for_session["session-a"])

    def test_load_policy_uses_default_override_path_when_not_explicitly_disabled(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            home = Path(tmp)
            override = home / ".config" / "paulshaclaw" / "policy.override.yaml"
            override.parent.mkdir(parents=True, exist_ok=True)
            override.write_text(json.dumps({"disable_rules": ["jwt"]}), encoding="utf-8")
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                base = mod.load_policy(override_path=None)
                overridden = mod.load_policy()
            finally:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        self.assertNotIn("jwt", base.disabled_rules)
        self.assertIn("jwt", overridden.disabled_rules)

    def test_override_supports_global_disable_local_regex_and_project_default(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "disable_rules": ["jwt"],
                "append_regex_rules": [{"id": "local_customer", "detector": "regex", "pattern": "ACME-INTERNAL-[0-9]+", "severity": "medium", "description": "local customer marker"}],
                "project_defaults": [{"project": "personal-notes", "level": "private", "reason": "local override"}]
            }), encoding="utf-8")
            policy = mod.load_policy(override_path=override)
        self.assertIn("jwt", policy.disabled_rules)
        self.assertIn("local_customer", policy.secret_rules)
        self.assertEqual(policy.classification.project_defaults["personal-notes"].level, "private")
        self.assertEqual(policy.classification.project_defaults["personal-notes"].reason, "local override")

    def test_project_default_override_keeps_roots_and_remotes(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "project_defaults": [{"project": "client", "level": "private", "reason": "client local", "roots": ["/work/client"], "remotes": ["git@example/client.git"]}]
            }), encoding="utf-8")
            policy = mod.load_policy(override_path=override)
        default = policy.classification.project_defaults["client"]
        self.assertEqual(default.roots, ("/work/client",))
        self.assertEqual(default.remotes, ("git@example/client.git",))

    def test_unsupported_major_version_fails_closed(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            policy_dir = Path(tmp)
            (policy_dir / "secrets.yaml").write_text('{"policy_version":"9.0.0","gitleaks":{"enabled":true},"rules":[]}', encoding="utf-8")
            (policy_dir / "classification.yaml").write_text('{"policy_version":"9.0.0","levels":["public","private","secret"],"unknown_project_default":"private","redaction_hit_default":"private","project_defaults":[]}', encoding="utf-8")
            (policy_dir / "boundaries.yaml").write_text('{"policy_version":"9.0.0","boundaries":[]}', encoding="utf-8")
            with self.assertRaises(mod.PolicyVersionError):
                mod.load_policy(default_dir=policy_dir)

    def test_is_rule_disabled_checks_global_and_session_overrides(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "disable_rules": ["jwt"],
                "disable_rules_for_session": {"session-a": ["github_pat"]}
            }), encoding="utf-8")
            policy = mod.load_policy(override_path=override)
        self.assertTrue(mod.is_rule_disabled(policy, "jwt", "session-a"))
        self.assertTrue(mod.is_rule_disabled(policy, "github_pat", "session-a"))
        self.assertFalse(mod.is_rule_disabled(policy, "github_pat", "session-b"))

    def test_append_regex_rule_cannot_replace_default_rule(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "append_regex_rules": [{"id": "github_pat", "detector": "regex", "pattern": "MALICIOUS", "severity": "low", "description": "bad override"}]
            }), encoding="utf-8")
            with self.assertRaises(mod.PolicyError):
                mod.load_policy(override_path=override)

    def test_project_default_override_can_clear_roots_and_remotes(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "project_defaults": [{"project": "paulshaclaw", "level": "private", "reason": "local clear", "roots": [], "remotes": []}]
            }), encoding="utf-8")
            policy = mod.load_policy(override_path=override)
        default = policy.classification.project_defaults["paulshaclaw"]
        self.assertEqual(default.roots, ())
        self.assertEqual(default.remotes, ())

    def test_append_regex_rule_rejects_invalid_pattern(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "append_regex_rules": [{"id": "local_bad", "detector": "regex", "pattern": "[unclosed", "severity": "medium", "description": "bad regex"}]
            }), encoding="utf-8")
            with self.assertRaises(mod.PolicyError):
                mod.load_policy(override_path=override)

    def test_append_regex_rule_rejects_empty_rule_id(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "append_regex_rules": [{"id": "", "detector": "regex", "pattern": "ACME", "severity": "medium", "description": "empty id"}]
            }), encoding="utf-8")
            with self.assertRaises(mod.PolicyError):
                mod.load_policy(override_path=override)

    def test_append_regex_rule_rejects_non_string_fields(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "append_regex_rules": [{"id": 123, "detector": "regex", "pattern": "ACME", "severity": "medium", "description": "numeric id"}]
            }), encoding="utf-8")
            with self.assertRaises(mod.PolicyError):
                mod.load_policy(override_path=override)

    def test_secret_rule_missing_required_field_raises_policy_error(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            policy_dir = Path(tmp)
            (policy_dir / "secrets.yaml").write_text(json.dumps({
                "policy_version": "0.1.0",
                "rules": [{"id": "local_secret", "pattern": "ACME", "severity": "medium", "description": "missing detector"}]
            }), encoding="utf-8")
            (policy_dir / "classification.yaml").write_text(json.dumps({
                "policy_version": "0.1.0",
                "levels": ["public", "private", "secret"],
                "unknown_project_default": "private",
                "redaction_hit_default": "private",
                "project_defaults": []
            }), encoding="utf-8")
            (policy_dir / "boundaries.yaml").write_text(json.dumps({
                "policy_version": "0.1.0",
                "boundaries": []
            }), encoding="utf-8")
            with self.assertRaisesRegex(mod.PolicyError, "detector|missing required field"):
                mod.load_policy(default_dir=policy_dir)


class RedactionTests(unittest.TestCase):
    def test_regex_redacts_entire_lines_and_reports_hits_without_secret_text(self):
        mod = load_policy(self)
        policy = mod.load_policy(override_path=None)
        text = "safe line\nAuthorization: Bearer sk-prod-secret-token\nnext line\n"
        result = mod.redact_lines(text, policy=policy, session_ref="session-a", boundary="external_to_raw")
        self.assertIn("safe line", result.text)
        self.assertIn("[REDACTED LINE:", result.text)
        self.assertNotIn("sk-prod-secret-token", result.text)
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(result.stage, "hook")
        self.assertEqual(result.hits[0].line_no, 2)

    def test_regex_patterns_are_compiled_once_per_rule(self):
        mod = load_policy(self)
        policy = mod.load_policy(override_path=None)
        redaction_mod = importlib.import_module("paulsha_hippo.policy.redaction")
        original_compile = redaction_mod.re.compile
        observed: list[str] = []

        def counting_compile(pattern, *args, **kwargs):
            observed.append(pattern)
            return original_compile(pattern, *args, **kwargs)

        redaction_mod.re.compile = counting_compile
        try:
            mod.redact_lines(
                f"safe line\nstill safe\ntoken=ghp_1234567890abcdefghijklmnopqrstuv\n",
                policy=policy,
                session_ref="session-a",
                boundary="external_to_raw",
            )
        finally:
            redaction_mod.re.compile = original_compile
        self.assertEqual(observed.count(policy.secret_rules["github_pat"].pattern), 1)


class GitleaksTests(unittest.TestCase):
    def test_gitleaks_json_hits_are_converted_to_policy_hits(self):
        mod = load_policy(self)
        report = [{"RuleID": "generic-api-key", "StartLine": 3}]
        hits = mod.parse_gitleaks_report(json.dumps(report))
        self.assertEqual(hits[0].rule_id, "generic-api-key")
        self.assertEqual(hits[0].detector, "gitleaks")
        self.assertEqual(hits[0].line_no, 3)

    def test_gitleaks_failure_raises_policy_error(self):
        mod = load_policy(self)
        def failing_runner(*_args, **_kwargs):
            raise FileNotFoundError("gitleaks")
        with self.assertRaises(mod.PolicyExecutionError):
            mod.run_gitleaks("content", runner=failing_runner)

    def test_run_gitleaks_uses_os_temp_dir_not_current_worktree(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            root = Path(tmp)
            cwd = Path.cwd()
            observed: dict[str, object] = {}

            def runner(args, **_kwargs):
                source = Path(args[args.index("--source") + 1])
                report = Path(args[args.index("--report-path") + 1])
                observed["source"] = source
                observed["report"] = report
                observed["entries"] = sorted(path.name for path in root.iterdir())
                report.write_text("[]", encoding="utf-8")
                return mod.CompletedGitleaks(0, "", "")

            os.chdir(root)
            try:
                hits = mod.run_gitleaks("safe text\n", runner=runner)
            finally:
                os.chdir(cwd)

            self.assertEqual(hits, ())
            self.assertEqual(observed["entries"], [])
            source = observed["source"]
            report = observed["report"]
            self.assertFalse(source == root or root in source.parents)
            self.assertFalse(report == root or root in report.parents)
            self.assertEqual(sorted(path.name for path in root.iterdir()), [])

    def test_raw_to_distilled_gitleaks_only_hit_is_redacted(self):
        mod = load_policy(self)
        text = "safe\nsecret value that regex does not catch\n"
        def runner(*_args, **_kwargs):
            return mod.CompletedGitleaks(1, json.dumps([{"RuleID": "generic-api-key", "StartLine": 2}]), "")
        result = mod.check_boundary("raw_to_distilled", text, project_slug="_unknown", session_ref="s1", gitleaks_runner=runner)
        self.assertIn("[REDACTED LINE: generic-api-key x1]", result.text)
        self.assertNotIn("secret value", result.text)

    def test_gitleaks_multiline_hit_redacts_every_reported_line(self):
        mod = load_policy(self)
        text = "safe\nsecret line 1\nsecret line 2\nsafe after\n"
        def runner(*_args, **_kwargs):
            return mod.CompletedGitleaks(
                1,
                json.dumps([{"RuleID": "generic-api-key", "StartLine": 2, "EndLine": 3}]),
                "",
            )
        result = mod.check_boundary("raw_to_distilled", text, project_slug="_unknown", session_ref="s1", gitleaks_runner=runner)
        redacted_lines = result.text.splitlines()
        self.assertEqual(redacted_lines[1], "[REDACTED LINE: generic-api-key x1]")
        self.assertEqual(redacted_lines[2], "[REDACTED LINE: generic-api-key x1]")
        self.assertNotIn("secret line 1", result.text)
        self.assertNotIn("secret line 2", result.text)


class ClassificationAndAuditTests(unittest.TestCase):
    def test_unknown_project_defaults_private_and_redaction_hit_downgrades(self):
        mod = load_policy(self)
        policy = mod.load_policy(override_path=None)
        no_hit = mod.classify_artifact(policy=policy, project_slug="_unknown", redaction_hits=())
        self.assertEqual(no_hit.level, "private")
        hit = mod.classify_artifact(policy=policy, project_slug="paulshaclaw", redaction_hits=(mod.PolicyHit("github_pat", "regex", 1, "redact"),))
        self.assertEqual(hit.level, "private")

    def test_redaction_hit_does_not_downgrade_secret_project_default(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            override = Path(tmp) / "policy.override.yaml"
            override.write_text(json.dumps({
                "project_defaults": [{"project": "client", "level": "secret", "reason": "client secret"}]
            }), encoding="utf-8")
            policy = mod.load_policy(override_path=override)
        result = mod.classify_artifact(
            policy=policy,
            project_slug="client",
            redaction_hits=(mod.PolicyHit("github_pat", "regex", 1, "redact"),),
        )
        self.assertEqual(result.level, "secret")

    def test_audit_writer_omits_secret_text(self):
        mod = load_policy(self)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.jsonl"
            event = mod.PolicyAuditEvent(boundary="external_to_raw", component="hook", session_ref="s1", policy_version="0.1.0", effective_policy_hash="hash", rule_id="github_pat", detector="regex", line_no=2, action="redact")
            mod.append_policy_audit(path, event)
            content = path.read_text(encoding="utf-8")
        record = json.loads(content)
        for field in ("ts", "boundary", "component", "session_ref", "policy_version", "effective_policy_hash", "rule_id", "detector", "line_no", "action"):
            self.assertIn(field, record)
        self.assertEqual(record["component"], "hook")
        self.assertEqual(record["session_ref"], "s1")
        self.assertEqual(record["detector"], "regex")
        self.assertEqual(record["action"], "redact")
        self.assertNotIn("ghp_", content)

    def test_audit_event_rejects_raw_text_fields(self):
        mod = load_policy(self)
        with self.assertRaises(TypeError):
            mod.PolicyAuditEvent(boundary="external_to_raw", component="hook", session_ref="s1", policy_version="0.1.0", effective_policy_hash="hash", rule_id="github_pat", detector="regex", line_no=2, action="redact", raw_line="ghp_secret")


class BoundaryTests(unittest.TestCase):
    def test_deferred_boundary_check_is_not_executable_in_mvp(self):
        mod = load_policy(self)
        with self.assertRaisesRegex(mod.PolicyExecutionError, "deferred|MVP|not executable"):
            mod.check_boundary("distilled_to_canonical", "safe text", project_slug="_unknown", session_ref="s1")

    def test_raw_to_distilled_boundary_returns_redacted_text_classification_and_metadata(self):
        mod = load_policy(self)
        result = mod.check_boundary("raw_to_distilled", "token=ghp_1234567890abcdefghijklmnopqrstuv", project_slug="paulshaclaw", session_ref="s1", gitleaks_runner=lambda *_a, **_k: mod.CompletedGitleaks(0, "[]", ""))
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuv", result.text)
        self.assertEqual(result.classification.level, "private")
        self.assertEqual(result.ledger_metadata["redaction_hits"], 1)
        self.assertIn("github_pat", result.ledger_metadata["redaction_types"])
        self.assertIn(result.ledger_metadata["redaction_stage"], {"importer", "both"})
        self.assertEqual(result.ledger_metadata["policy_version"], result.policy.policy_version)
        self.assertEqual(result.ledger_metadata["effective_policy_hash"], result.policy.effective_policy_hash)

    def test_check_boundary_uses_default_override_when_no_policy_is_passed(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            home = Path(tmp)
            override = home / ".config" / "paulshaclaw" / "policy.override.yaml"
            override.parent.mkdir(parents=True, exist_ok=True)
            override.write_text(json.dumps({"disable_rules_for_session": {"s1": ["github_pat"]}}), encoding="utf-8")
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                result = mod.check_boundary(
                    "external_to_raw",
                    "token=ghp_1234567890abcdefghijklmnopqrstuv",
                    project_slug="paulshaclaw",
                    session_ref="s1",
                )
            finally:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        self.assertEqual(result.hits, ())

    def test_failure_stub_contains_metadata_only(self):
        mod = load_policy(self)
        with TemporaryDirectory() as tmp:
            stub = mod.write_failure_stub(Path(tmp), session_ref="s1", source_tool="codex", boundary="raw_to_distilled", error_class="PolicyExecutionError", policy_version="0.1.0", effective_policy_hash="hash", ledger_available=False)
            text = stub.read_text(encoding="utf-8")
        self.assertIn("ledger_status", text)
        self.assertNotIn("conversation", text)
        self.assertNotIn("ghp_", text)

    def test_fail_closed_retry_exhaustion_writes_stub_unlinks_queue_and_publishes_no_inbox(self):
        mod = load_policy(self)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            queue.write_text("secret=ghp_1234567890abcdefghijklmnopqrstuv", encoding="utf-8")
            result = mod.handle_policy_failure(queue_path=queue, failed_dir=root / "_failed", inbox_path=inbox, session_ref="s1", source_tool="codex", boundary="raw_to_distilled", error=mod.PolicyExecutionError("boom"), policy=mod.load_policy(override_path=None), ledger_available=True)
            self.assertFalse(queue.exists())
            self.assertFalse(inbox.exists())
            self.assertTrue(result.stub_path.exists())
            self.assertNotIn("ghp_", result.stub_path.read_text(encoding="utf-8"))

    def test_boundary_retries_gitleaks_failure_before_stub(self):
        mod = load_policy(self)
        calls = []
        def failing_runner(*_args, **_kwargs):
            calls.append("call")
            raise FileNotFoundError("gitleaks")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            queue.write_text("safe text", encoding="utf-8")
            result = mod.process_queue_with_policy(queue_path=queue, inbox_path=inbox, failed_dir=root / "_failed", boundary="raw_to_distilled", project_slug="_unknown", session_ref="s1", source_tool="codex", gitleaks_runner=failing_runner)
            self.assertEqual(len(calls), 3)
            self.assertEqual(result.status, "policy-error")
            self.assertFalse(queue.exists())
            self.assertFalse(inbox.exists())
            self.assertTrue(result.stub_path.exists())
            self.assertNotIn("safe text", result.stub_path.read_text(encoding="utf-8"))

    def test_process_queue_retries_policy_load_failure_before_stub(self):
        mod = load_policy(self)
        boundary_mod = importlib.import_module("paulsha_hippo.policy.boundary")
        calls = []
        original_loader = boundary_mod.load_policy
        def failing_loader(*_args, **_kwargs):
            calls.append("call")
            raise mod.PolicyExecutionError("load failed")
        boundary_mod.load_policy = failing_loader
        try:
            with temporary_directory() as tmp:
                root = Path(tmp)
                queue = root / "queue.json"
                inbox = root / "inbox.md"
                queue.write_text("safe text", encoding="utf-8")
                result = mod.process_queue_with_policy(queue_path=queue, inbox_path=inbox, failed_dir=root / "_failed", boundary="raw_to_distilled", project_slug="_unknown", session_ref="s1", source_tool="codex")
                self.assertEqual(len(calls), 3)
                self.assertEqual(result.status, "policy-error")
                self.assertFalse(queue.exists())
                self.assertFalse(inbox.exists())
                self.assertTrue(result.stub_path.exists())
                self.assertNotIn("safe text", result.stub_path.read_text(encoding="utf-8"))
        finally:
            boundary_mod.load_policy = original_loader

    def test_process_queue_read_failure_writes_stub_and_no_inbox(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            queue.mkdir()
            result = mod.process_queue_with_policy(queue_path=queue, inbox_path=inbox, failed_dir=root / "_failed", boundary="raw_to_distilled", project_slug="_unknown", session_ref="s1", source_tool="codex")
            self.assertEqual(result.status, "policy-error")
            self.assertTrue(queue.exists())
            self.assertTrue(queue.is_dir())
            self.assertFalse(inbox.exists())
            self.assertTrue(result.stub_path.exists())
            stub = json.loads(result.stub_path.read_text(encoding="utf-8"))
            self.assertEqual(stub["error_class"], "IsADirectoryError")
            self.assertNotIn("conversation", result.stub_path.read_text(encoding="utf-8"))

    def test_process_queue_unknown_boundary_unlinks_queue_writes_stub_and_no_inbox(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            queue.write_text("safe text", encoding="utf-8")
            result = mod.process_queue_with_policy(queue_path=queue, inbox_path=inbox, failed_dir=root / "_failed", boundary="unknown_boundary", project_slug="_unknown", session_ref="s1", source_tool="codex")
            self.assertEqual(result.status, "policy-error")
            self.assertFalse(queue.exists())
            self.assertFalse(inbox.exists())
            self.assertTrue(result.stub_path.exists())
            stub_text = result.stub_path.read_text(encoding="utf-8")
            stub = json.loads(stub_text)
            self.assertEqual(stub["error_class"], "PolicyExecutionError")
            self.assertNotIn("safe text", stub_text)

    def test_process_queue_deferred_boundary_retries_writes_stub_and_no_inbox(self):
        mod = load_policy(self)
        boundary_mod = importlib.import_module("paulsha_hippo.policy.boundary")
        policy = mod.load_policy(override_path=None)
        calls = []
        original_check_boundary = boundary_mod.check_boundary

        def counting_check_boundary(*args, **kwargs):
            calls.append(args[0] if args else kwargs["boundary"])
            return original_check_boundary(*args, **kwargs)

        boundary_mod.check_boundary = counting_check_boundary
        try:
            with temporary_directory() as tmp:
                root = Path(tmp)
                queue = root / "queue.json"
                inbox = root / "inbox.md"
                queue.write_text("safe text", encoding="utf-8")
                result = mod.process_queue_with_policy(
                    queue_path=queue,
                    inbox_path=inbox,
                    failed_dir=root / "_failed",
                    boundary="distilled_to_canonical",
                    project_slug="_unknown",
                    session_ref="s1",
                    source_tool="codex",
                    policy=policy,
                )
                self.assertEqual(calls, ["distilled_to_canonical"] * policy.boundaries["distilled_to_canonical"].retry_count)
                self.assertEqual(result.status, "policy-error")
                self.assertFalse(queue.exists())
                self.assertFalse(inbox.exists())
                self.assertTrue(result.stub_path.exists())
                stub_text = result.stub_path.read_text(encoding="utf-8")
                stub = json.loads(stub_text)
                self.assertEqual(stub["boundary"], "distilled_to_canonical")
                self.assertEqual(stub["error_class"], "PolicyExecutionError")
                self.assertEqual(stub["policy_version"], policy.policy_version)
                self.assertEqual(stub["effective_policy_hash"], policy.effective_policy_hash)
                self.assertNotIn("safe text", stub_text)
        finally:
            boundary_mod.check_boundary = original_check_boundary

    def test_process_queue_writes_safe_rule_level_audit_records(self):
        mod = load_policy(self)
        with temporary_directory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            audit = root / "policy-audit.jsonl"
            original_line = "token=ghp_1234567890abcdefghijklmnopqrstuv"
            queue.write_text(f"safe line\n{original_line}\n", encoding="utf-8")

            result = mod.process_queue_with_policy(
                queue_path=queue,
                inbox_path=inbox,
                failed_dir=root / "_failed",
                boundary="raw_to_distilled",
                project_slug="_unknown",
                session_ref="s1",
                source_tool="codex",
                gitleaks_runner=lambda *_a, **_k: mod.CompletedGitleaks(0, "[]", ""),
                audit_path=audit,
            )

            self.assertEqual(result.status, "ok")
            records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(
                set(record),
                {
                    "action",
                    "boundary",
                    "component",
                    "detector",
                    "effective_policy_hash",
                    "line_no",
                    "policy_version",
                    "rule_id",
                    "session_ref",
                    "ts",
                },
            )
            self.assertEqual(record["boundary"], "raw_to_distilled")
            self.assertEqual(record["component"], "importer")
            self.assertEqual(record["session_ref"], "s1")
            self.assertEqual(record["rule_id"], "github_pat")
            self.assertEqual(record["detector"], "regex")
            self.assertEqual(record["line_no"], 2)
            self.assertEqual(record["action"], "redact")
            audit_text = audit.read_text(encoding="utf-8")
            self.assertNotIn("ghp_", audit_text)
            self.assertNotIn(original_line, audit_text)
            self.assertNotIn("safe line", audit_text)

    def test_process_queue_appends_audit_before_publishing_inbox(self):
        mod = load_policy(self)
        boundary_mod = importlib.import_module("paulsha_hippo.policy.boundary")
        original_append_policy_audits = boundary_mod.append_policy_audits
        with temporary_directory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            audit = root / "policy-audit.jsonl"
            queue.write_text("token=ghp_1234567890abcdefghijklmnopqrstuv\n", encoding="utf-8")
            observed: dict[str, bool] = {}

            def checking_append_policy_audits(path, events):
                observed["inbox_exists_before_audit"] = inbox.exists()
                return original_append_policy_audits(path, events)

            boundary_mod.append_policy_audits = checking_append_policy_audits
            try:
                result = mod.process_queue_with_policy(
                    queue_path=queue,
                    inbox_path=inbox,
                    failed_dir=root / "_failed",
                    boundary="raw_to_distilled",
                    project_slug="_unknown",
                    session_ref="s1",
                    source_tool="codex",
                    gitleaks_runner=lambda *_a, **_k: mod.CompletedGitleaks(0, "[]", ""),
                    audit_path=audit,
                )
                self.assertEqual(result.status, "ok")
                self.assertFalse(observed["inbox_exists_before_audit"])
                self.assertTrue(inbox.exists())
            finally:
                boundary_mod.append_policy_audits = original_append_policy_audits

    def test_publish_failure_unlinks_queue_and_writes_stub(self):
        mod = load_policy(self)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            inbox.mkdir()
            queue.write_text("secret=ghp_1234567890abcdefghijklmnopqrstuv", encoding="utf-8")
            result = mod.process_queue_with_policy(queue_path=queue, inbox_path=inbox, failed_dir=root / "_failed", boundary="raw_to_distilled", project_slug="_unknown", session_ref="s1", source_tool="codex", gitleaks_runner=lambda *_a, **_k: mod.CompletedGitleaks(0, "[]", ""))
            self.assertEqual(result.status, "policy-error")
            self.assertFalse(queue.exists())
            self.assertTrue(result.stub_path.exists())
            self.assertNotIn("ghp_", result.stub_path.read_text(encoding="utf-8"))

    def test_stub_write_failure_still_unlinks_queue(self):
        mod = load_policy(self)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.json"
            inbox = root / "inbox.md"
            failed_dir = root / "_failed"
            failed_dir.write_text("not a directory", encoding="utf-8")
            queue.write_text("secret=ghp_1234567890abcdefghijklmnopqrstuv", encoding="utf-8")
            with self.assertRaises((FileExistsError, NotADirectoryError, OSError)):
                mod.handle_policy_failure(queue_path=queue, failed_dir=failed_dir, inbox_path=inbox, session_ref="s1", source_tool="codex", boundary="raw_to_distilled", error=mod.PolicyExecutionError("boom"), policy=mod.load_policy(override_path=None), ledger_available=True)
            self.assertFalse(queue.exists())
            self.assertFalse(inbox.exists())
