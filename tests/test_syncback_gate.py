import unittest
from typing import get_type_hints
from unittest.mock import patch
from contextlib import contextmanager
from pathlib import Path
import subprocess
import shutil
import sys
import uuid

from paulsha_hippo.lib.lifecycle import schema as lifecycle_schema
from paulsha_hippo.syncback import gate


CANONICAL_STAGE3_REQUIRED_FIELDS = {
    "phase",
    "project",
    "slice_id",
    "artifact_kind",
    "version",
    "created_at",
    "created_by",
    "source_session",
    "gate_required",
    "checksum",
}


@contextmanager
def _repo_tempdir():
    root = (
        Path(__file__).resolve().parents[1]
        / ".scratch_task2"
        / f"test-syncback-gate-{uuid.uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class SchemaConditionTest(unittest.TestCase):
    def test_check_schema_unextended_returns_conditionresult_and_passes_for_canonical_fields(self):
        res = gate._check_schema_unextended()
        self.assertTrue(hasattr(res, 'id'))
        self.assertEqual(res.id, 'schema_unextended')
        self.assertTrue(hasattr(res, 'passed'))
        self.assertIsInstance(res.passed, bool)
        self.assertEqual(set(lifecycle_schema.REQUIRED_FRONTMATTER_FIELDS), CANONICAL_STAGE3_REQUIRED_FIELDS)
        self.assertTrue(res.passed)
        self.assertEqual(res.detail, "")

    def test_check_schema_unextended_fails_when_required_fields_include_extra(self):
        extra_required = tuple(lifecycle_schema.REQUIRED_FRONTMATTER_FIELDS) + ("unexpected_required",)

        with patch.object(lifecycle_schema, "REQUIRED_FRONTMATTER_FIELDS", extra_required):
            res = gate._check_schema_unextended()

        self.assertFalse(res.passed)
        self.assertIn("extra", res.detail)
        self.assertIn("unexpected_required", res.detail)

    def test_check_schema_unextended_fails_when_required_fields_are_missing(self):
        missing_required = tuple(
            field
            for field in lifecycle_schema.REQUIRED_FRONTMATTER_FIELDS
            if field != "checksum"
        )

        with patch.object(lifecycle_schema, "REQUIRED_FRONTMATTER_FIELDS", missing_required):
            res = gate._check_schema_unextended()

        self.assertFalse(res.passed)
        self.assertIn("missing", res.detail)
        self.assertIn("checksum", res.detail)

    def test_sync_manifest_contains_expected_paths(self):
        expected = (
            "paulshaclaw/memory/",
            "paulsha_hippo/hooks/",
            "paulsha_hippo/hooks/install.sh",
            "paulsha_hippo/hooks/uninstall.sh",
        )
        self.assertIsInstance(gate.SYNC_MANIFEST, tuple)
        self.assertEqual(tuple(expected), tuple(gate.SYNC_MANIFEST))

    def test_contract_types_match_task1_plan(self):
        self.assertTrue(gate.ConditionResult.__dataclass_params__.frozen)
        self.assertTrue(gate.GateVerdict.__dataclass_params__.frozen)
        self.assertIs(get_type_hints(gate.GateVerdict)["ts"], str)


class FileConditionTest(unittest.TestCase):
    def test_check_evidence_present_passes_when_files_exist_and_nonempty(self):
        with _repo_tempdir() as repo_root:
            evidence_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory" / "evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "README.md").write_text("evidence")
            (evidence_dir / "stage2-integration-template.md").write_text("template")

            res = gate._check_evidence_present(repo_root)
            self.assertEqual(res.id, 'evidence_present')
            self.assertTrue(res.passed)

    def test_check_evidence_present_fails_when_missing_or_empty(self):
        with _repo_tempdir() as repo_root:
            # missing files
            res = gate._check_evidence_present(repo_root)
            self.assertFalse(res.passed)
            self.assertIn('missing', res.detail)

            # empty file
            evidence_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory" / "evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "README.md").write_text("")
            (evidence_dir / "stage2-integration-template.md").write_text("\n")
            res2 = gate._check_evidence_present(repo_root)
            self.assertFalse(res2.passed)
            self.assertIn('empty', res2.detail)

    def test_check_review_clear_passes_for_mergeable_conclusion(self):
        for heading in ("### Conclusion", "### 結論"):
            with self.subTest(heading=heading):
                with _repo_tempdir() as repo_root:
                    docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
                    docs_dir.mkdir(parents=True)
                    (docs_dir / "review.md").write_text(
                        f'# review\n\n{heading}\n\n- 結論：可合併。\n'
                    )

                    res = gate._check_review_clear(repo_root)

                    self.assertEqual(res.id, 'review_clear')
                    self.assertTrue(res.passed)

    def test_check_review_clear_fails_on_blocking_or_missing(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)

            # missing file
            res = gate._check_review_clear(repo_root)
            self.assertFalse(res.passed)
            self.assertIn('missing', res.detail)

            # present but missing conclusion section
            (docs_dir / "review.md").write_text('# review\n\nNo conclusion here\n')
            res2 = gate._check_review_clear(repo_root)
            self.assertFalse(res2.passed)
            self.assertIn('结論', res2.detail or '結論')

            # blocking conclusion
            (docs_dir / "review.md").write_text('# review\n\n## 結論\n\n- 結論：不可合併。\n')
            res3 = gate._check_review_clear(repo_root)
            self.assertFalse(res3.passed)
            self.assertIn('不可', res3.detail)

    def test_check_review_clear_rejects_noncanonical_conclusion_headings(self):
        for heading in (
            "## Conclusion Draft",
            "### Conclusion Draft",
            "## 結論草稿",
            "### 結論草稿",
        ):
            with self.subTest(heading=heading):
                with _repo_tempdir() as repo_root:
                    docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
                    docs_dir.mkdir(parents=True)
                    (docs_dir / "review.md").write_text(
                        f'# review\n\n{heading}\n\n- Conclusion: mergeable.\n'
                    )

                    res = gate._check_review_clear(repo_root)

                    self.assertFalse(res.passed)
                    self.assertIn('missing', res.detail)

    def test_check_review_clear_fails_closed_for_ambiguous_conclusion(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Conclusion\n\n- Conclusion: needs follow-up discussion.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertFalse(res.passed)
            self.assertIn('unrecognized', res.detail)

    def test_check_review_clear_fails_closed_for_invalid_utf8_review(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_bytes(b"## \xe7\xb5\x90\xe8\xab\x96\n\n- \xff\xfe\xfa\n")

            res = gate._check_review_clear(repo_root)

            self.assertFalse(res.passed)
            self.assertIn('unreadable', res.detail)

    def test_check_review_clear_fails_closed_for_unreadable_review(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            review_path = docs_dir / "review.md"
            review_path.write_text('# review\n\n## 結論\n\n- 結論：可合併。\n')
            review_path.chmod(0)
            try:
                res = gate._check_review_clear(repo_root)
            finally:
                review_path.chmod(0o644)

            self.assertFalse(res.passed)
            self.assertIn('unreadable', res.detail)

    def test_check_review_clear_fails_for_english_negated_merge_phrases(self):
        phrases = (
            'do not merge',
            'not ready to merge',
            'not approved for merge',
        )

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                with _repo_tempdir() as repo_root:
                    docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
                    docs_dir.mkdir(parents=True)
                    (docs_dir / "review.md").write_text(
                        f'# review\n\n## Conclusion\n\n- Conclusion: {phrase}.\n'
                    )

                    res = gate._check_review_clear(repo_root)

                    self.assertFalse(res.passed)
    def test_check_review_clear_passes_for_mergeable_conclusion_with_no_blocking_issues(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Notes\n\n- Reviewer note: do not merge until conclusion is updated.\n\n'
                '## Conclusion\n\n- Conclusion: mergeable. No blocking issues.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertTrue(res.passed)

    def test_check_review_clear_fails_when_english_negated_blocker_is_followed_by_positive_blocker(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Conclusion\n\n'
                '- Conclusion: mergeable. No blockers in the resolved comments.\n'
                '- However, blocker: migration rollback is still unverified.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertFalse(res.passed)
            self.assertIn('blocker', res.detail.lower())

    def test_check_review_clear_passes_when_failing_word_is_non_blocking_context(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Conclusion\n\n- Conclusion: mergeable. No failing tests in reviewed scope.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertTrue(res.passed)

    def test_check_review_clear_fails_when_zh_tw_negated_blocker_is_followed_by_positive_blocker(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## 結論\n\n'
                '- 結論：可合併，無阻斷性問題。\n'
                '- 但仍有阻斷項目：需補齊回滾驗證。\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertFalse(res.passed)
            self.assertIn('阻斷', res.detail)

    def test_check_review_clear_passes_for_zh_tw_canonical_pass_wording(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## 結論\n\n- 結論：可合併，無阻斷性問題。\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertTrue(res.passed)

    def test_check_review_clear_fails_for_mixed_mergeable_and_blocking_markers(self):
        for marker in ("BLOCKING", "BLOCKER"):
            with self.subTest(marker=marker):
                with _repo_tempdir() as repo_root:
                    docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
                    docs_dir.mkdir(parents=True)
                    (docs_dir / "review.md").write_text(
                        f'# review\n\n## Conclusion\n\n- Conclusion: mergeable.\n- {marker}\n'
                    )

                    res = gate._check_review_clear(repo_root)

                    self.assertFalse(res.passed)
                    self.assertIn(marker, res.detail)

    def test_check_review_clear_fails_for_lowercase_blocking_markers(self):
        for marker in ("blocking", "blocker", "blockers"):
            with self.subTest(marker=marker):
                with _repo_tempdir() as repo_root:
                    docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
                    docs_dir.mkdir(parents=True)
                    (docs_dir / "review.md").write_text(
                        f'# review\n\n## Conclusion\n\n- Conclusion: mergeable.\n- {marker} issue remains.\n'
                    )

                    res = gate._check_review_clear(repo_root)

                    self.assertFalse(res.passed)
                    self.assertIn(marker, res.detail)

    def test_check_review_clear_passes_for_negated_blocked_wording(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Conclusion\n\n- Conclusion: No blocked items. Mergeable.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertTrue(res.passed)

    def test_check_review_clear_fails_for_positive_blocked_wording(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True)
            (docs_dir / "review.md").write_text(
                '# review\n\n## Conclusion\n\n- Conclusion: Blocked on issue #123.\n'
            )

            res = gate._check_review_clear(repo_root)

            self.assertFalse(res.passed)
            self.assertIn('Blocked', res.detail)


def _write_syncback_gate_artifacts(repo_root: Path, *, review_heading: str = "## 結論", review_body: str = "- 結論：可合併。\n") -> None:
    evidence_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "README.md").write_text("evidence\n")
    (evidence_dir / "stage2-integration-template.md").write_text("template\n")

    docs_dir = evidence_dir.parent
    (docs_dir / "review.md").write_text(f"# review\n\n{review_heading}\n\n{review_body}")


class EvaluateGateTest(unittest.TestCase):
    def test_default_test_runner_invokes_unittest_via_subprocess(self):
        modules = (
            "paulsha_hippo.tests.test_importer_cli",
            "paulsha_hippo.tests.test_classifier",
        )

        with patch(
            "paulsha_hippo.syncback.gate.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
            create=True,
        ) as run, patch(
            "paulsha_hippo.syncback.gate.unittest.defaultTestLoader.loadTestsFromNames",
            side_effect=AssertionError("in-process loader must not be used"),
        ):
            ok = gate._default_test_runner(modules)

        self.assertTrue(ok)
        run.assert_called_once_with(
            [sys.executable, "-m", "unittest", *modules],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_default_test_runner_fails_closed_when_unittest_reports_all_skipped(self):
        modules = ("paulsha_hippo.tests.test_importer_cli",)

        with patch(
            "paulsha_hippo.syncback.gate.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="s\n----------------------------------------------------------------------\nRan 1 test in 0.000s\n\nOK (skipped=1)\n",
                stderr="",
            ),
            create=True,
        ):
            ok = gate._default_test_runner(modules)

        self.assertFalse(ok)

    def test_default_test_runner_passes_when_unittest_runs_non_skipped_tests(self):
        modules = ("paulsha_hippo.tests.test_importer_cli",)

        with patch(
            "paulsha_hippo.syncback.gate.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=".s\n----------------------------------------------------------------------\nRan 2 tests in 0.000s\n\nOK (skipped=1)\n",
                stderr="",
            ),
            create=True,
        ):
            ok = gate._default_test_runner(modules)

        self.assertTrue(ok)

    def test_evaluate_gate_returns_ok_manifest_and_expected_conditions_when_all_checks_pass(self):
        with _repo_tempdir() as repo_root:
            _write_syncback_gate_artifacts(repo_root)
            calls = []

            def fake_runner(modules):
                calls.append(tuple(modules))
                return True

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                test_runner=fake_runner,
            )

            self.assertTrue(verdict.ok)
            self.assertEqual(verdict.ts, "2026-06-06T00:00:00Z")
            self.assertEqual(verdict.sync_manifest, gate.SYNC_MANIFEST)
            self.assertEqual(
                {condition.id for condition in verdict.conditions},
                {"tests", "decay_evidence", "evidence_present", "review_clear", "schema_unextended"},
            )
            self.assertTrue(all(condition.passed for condition in verdict.conditions))
            self.assertEqual(calls, [gate.TESTS_CORE, gate.TESTS_DECAY])

    def test_evaluate_gate_returns_empty_manifest_when_test_runner_reports_failure(self):
        with _repo_tempdir() as repo_root:
            _write_syncback_gate_artifacts(repo_root)

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                test_runner=lambda modules: False,
            )

            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.sync_manifest, ())
            by_id = {condition.id: condition for condition in verdict.conditions}
            self.assertFalse(by_id["tests"].passed)
            self.assertFalse(by_id["decay_evidence"].passed)
            self.assertTrue(by_id["evidence_present"].passed)
            self.assertTrue(by_id["review_clear"].passed)
            self.assertTrue(by_id["schema_unextended"].passed)

    def test_evaluate_gate_fails_closed_when_runner_raises_for_core_tests(self):
        with _repo_tempdir() as repo_root:
            _write_syncback_gate_artifacts(repo_root)

            def fake_runner(modules):
                if tuple(modules) == gate.TESTS_CORE:
                    raise RuntimeError("runner exploded")
                return True

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                test_runner=fake_runner,
            )

            by_id = {condition.id: condition for condition in verdict.conditions}
            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.sync_manifest, ())
            self.assertFalse(by_id["tests"].passed)
            self.assertEqual(by_id["tests"].detail, "test runner raised")
            self.assertTrue(by_id["decay_evidence"].passed)

    def test_evaluate_gate_fails_closed_when_runner_raises_for_decay_tests(self):
        with _repo_tempdir() as repo_root:
            _write_syncback_gate_artifacts(repo_root)

            def fake_runner(modules):
                if tuple(modules) == gate.TESTS_DECAY:
                    raise RuntimeError("decay runner exploded")
                return True

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                test_runner=fake_runner,
            )

            by_id = {condition.id: condition for condition in verdict.conditions}
            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.sync_manifest, ())
            self.assertTrue(by_id["tests"].passed)
            self.assertFalse(by_id["decay_evidence"].passed)
            self.assertEqual(by_id["decay_evidence"].detail, "runner raised")

    def test_evaluate_gate_fails_test_conditions_without_running_tests_when_disabled(self):
        with _repo_tempdir() as repo_root:
            _write_syncback_gate_artifacts(repo_root)

            def fake_runner(_modules):
                raise AssertionError("test runner should not be called when run_tests=False")

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                run_tests=False,
                test_runner=fake_runner,
            )

            by_id = {condition.id: condition for condition in verdict.conditions}
            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.sync_manifest, ())
            self.assertFalse(by_id["tests"].passed)
            self.assertFalse(by_id["decay_evidence"].passed)
            self.assertIn("disabled", by_id["tests"].detail.lower())
            self.assertIn("disabled", by_id["decay_evidence"].detail.lower())

    def test_evaluate_gate_fails_decay_evidence_when_decay_tests_pass_without_evidence(self):
        with _repo_tempdir() as repo_root:
            docs_dir = repo_root / "docs" / "superpowers" / "workstreams" / "stage2-paulsha-memory"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "review.md").write_text("# review\n\n## 結論\n\n- 結論：可合併。\n")
            calls = []

            def fake_runner(modules):
                calls.append(tuple(modules))
                return True

            verdict = gate.evaluate_gate(
                repo_root,
                now="2026-06-06T00:00:00Z",
                test_runner=fake_runner,
            )

            by_id = {condition.id: condition for condition in verdict.conditions}
            self.assertFalse(verdict.ok)
            self.assertEqual(calls, [gate.TESTS_CORE, gate.TESTS_DECAY])
            self.assertTrue(by_id["tests"].passed)
            self.assertFalse(by_id["decay_evidence"].passed)
            self.assertIn("evidence", by_id["decay_evidence"].detail.lower())
            self.assertFalse(by_id["evidence_present"].passed)


if __name__ == '__main__':
    unittest.main()
