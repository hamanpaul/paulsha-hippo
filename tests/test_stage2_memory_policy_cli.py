from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(__file__).resolve().parent
SECRET = "ghp_1234567890abcdefghijklmnopqrstuv"


def temporary_directory():
    return TemporaryDirectory(dir=TEMP_ROOT)


class MemoryPolicyCliTests(unittest.TestCase):
    def run_memory(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "paulsha_hippo", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def fake_gitleaks_env(
        self,
        root: Path,
        *,
        report: list[dict[str, object]] | None = None,
        exit_code: int = 0,
    ) -> dict[str, str]:
        script = root / "gitleaks"
        script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import os",
                    "import sys",
                    "",
                    "args = sys.argv[1:]",
                    'report_path = args[args.index("--report-path") + 1]',
                    'payload = json.loads(os.environ.get("PSC_TEST_GITLEAKS_REPORT", "[]"))',
                    'with open(report_path, "w", encoding="utf-8") as handle:',
                    "    json.dump(payload, handle)",
                    'sys.exit(int(os.environ.get("PSC_TEST_GITLEAKS_EXIT", "0")))',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{root}:{env.get('PATH', '')}"
        env["PSC_TEST_GITLEAKS_REPORT"] = json.dumps(report or [])
        env["PSC_TEST_GITLEAKS_EXIT"] = str(exit_code)
        return env

    def test_dry_run_policy_prints_safe_json_without_writing_artifact(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            inbox = root / "inbox.md"
            original_line = f"token={SECRET}"
            payload.write_text(f"safe line\n{original_line}\n", encoding="utf-8")
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "dry-run-policy",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["boundary"], "raw_to_distilled")
            self.assertEqual(summary["classification_level"], "private")
            self.assertEqual(summary["policy_version"], "0.1.0")
            self.assertIn("effective_policy_hash", summary)
            self.assertEqual(summary["hits"][0]["rule_id"], "github_pat")
            self.assertEqual(summary["hits"][0]["detector"], "regex")
            self.assertEqual(summary["hits"][0]["line_no"], 2)
            self.assertEqual(summary["hits"][0]["action"], "redact")
            self.assertEqual(summary["hits"][0]["boundary"], "raw_to_distilled")
            self.assertFalse(inbox.exists())
            self.assertEqual(list(root.glob("*.policy-audit.jsonl")), [])
            self.assertNotIn(SECRET, completed.stdout)
            self.assertNotIn(original_line, completed.stdout)

    def test_dry_run_policy_reports_skipped_override_entries(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            override = root / "policy.override.yaml"
            payload.write_text(f"token={SECRET}\n", encoding="utf-8")
            override.write_text(
                json.dumps({"disable_rules_for_session": {"s1": ["github_pat"]}}),
                encoding="utf-8",
            )
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "dry-run-policy",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                "--override",
                str(override),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["hits"], [])
            self.assertEqual(summary["classification_level"], "public")
            skipped = summary["skipped_overrides"]
            self.assertEqual(skipped[0]["rule_id"], "github_pat")
            self.assertEqual(skipped[0]["detector"], "regex")
            self.assertEqual(skipped[0]["line_no"], 1)
            self.assertEqual(skipped[0]["action"], "skipped")
            self.assertEqual(skipped[0]["boundary"], "raw_to_distilled")
            self.assertNotIn(SECRET, completed.stdout)

    def test_dry_run_policy_reports_non_utf8_payload_cleanly(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            payload.write_bytes(b"\xff\xfe")
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "dry-run-policy",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                env=env,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertIn(str(payload), completed.stderr)
            self.assertIn("utf-8", completed.stderr.lower())
            self.assertNotIn("Traceback", completed.stderr)

    def test_replay_writes_redacted_frontmatter_artifact_and_summary(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            out = root / "inbox.md"
            payload.write_text(f"safe line\ntoken={SECRET}\n", encoding="utf-8")
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "replay",
                "--session",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                "--out",
                str(out),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = out.read_text(encoding="utf-8")
            self.assertTrue(artifact.startswith("---\n"))
            self.assertIn("classification_level: private", artifact)
            self.assertIn("classification_reason: redaction hits present", artifact)
            self.assertIn("classification_policy_hash:", artifact)
            self.assertIn("classification_source: default_rule", artifact)
            self.assertIn("safe line", artifact)
            self.assertIn("[REDACTED LINE: github_pat x1]", artifact)
            self.assertNotIn(SECRET, artifact)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["out"], str(out))
            self.assertEqual(summary["classification_level"], "private")
            self.assertEqual(summary["redaction_hits"], 1)
            self.assertEqual(summary["redaction_types"], ["github_pat"])
            self.assertIn("effective_policy_hash", summary)
            self.assertEqual(summary["policy_version"], "0.1.0")
            self.assertNotIn(SECRET, completed.stdout)

    def test_replay_quotes_frontmatter_values_that_need_yaml_escaping(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            out = root / "inbox.md"
            override = root / "policy.override.yaml"
            payload.write_text("safe line\n", encoding="utf-8")
            override.write_text(
                json.dumps(
                    {
                        "project_defaults": [
                            {
                                "project": "client",
                                "level": "private",
                                "reason": "needs: review # keep quoted",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "replay",
                "--session",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "client",
                "--override",
                str(override),
                "--out",
                str(out),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = out.read_text(encoding="utf-8")
            self.assertRegex(artifact, r"classification_reason: [\"']needs: review # keep quoted[\"']")

    def test_replay_writes_safe_audit_log_for_redaction_hit(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            out = root / "inbox.md"
            audit = root / "inbox.policy-audit.jsonl"
            original_line = f"token={SECRET}"
            payload.write_text(f"safe line\n{original_line}\n", encoding="utf-8")
            env = self.fake_gitleaks_env(root)

            completed = self.run_memory(
                "replay",
                "--session",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                "--out",
                str(out),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
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
            self.assertNotIn(SECRET, audit_text)
            self.assertNotIn(original_line, audit_text)
            self.assertNotIn("safe line", audit_text)

    def test_replay_uses_gitleaks_path_for_gitleaks_only_hit(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            payload = root / "payload.txt"
            out = root / "inbox.md"
            payload.write_text("safe line\ngitleaks only marker\n", encoding="utf-8")
            env = self.fake_gitleaks_env(
                root,
                report=[{"RuleID": "gitleaks_custom", "StartLine": 2, "EndLine": 2}],
                exit_code=1,
            )

            completed = self.run_memory(
                "replay",
                "--session",
                "s1",
                "--payload-file",
                str(payload),
                "--project",
                "paulshaclaw",
                "--out",
                str(out),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = out.read_text(encoding="utf-8")
            self.assertIn("[REDACTED LINE: gitleaks_custom x1]", artifact)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["hits"][0]["rule_id"], "gitleaks_custom")
            self.assertEqual(summary["hits"][0]["detector"], "gitleaks")
            self.assertEqual(summary["hits"][0]["line_no"], 2)


if __name__ == "__main__":
    unittest.main()
