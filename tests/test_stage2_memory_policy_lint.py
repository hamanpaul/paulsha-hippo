from __future__ import annotations

import contextlib
import importlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


TEMP_ROOT = Path(__file__).resolve().parents[1]
GOOD = 'from paulsha_hippo import policy\n# memory-consumer\n\ndef write_memory():\n    policy.check_boundary("raw_to_distilled", "safe", project_slug="_unknown", session_ref="s")\n'
BAD = '# memory-consumer\ndef write_memory():\n    open("inbox.md", "w").write("unsafe")\n'
IMPORT_ONLY_BAD = 'from paulsha_hippo import policy\n# memory-consumer\ndef write_memory():\n    open("inbox.md", "w").write("unsafe")\n'
COMMENT_ONLY_BAD = '# memory-consumer\n# check_boundary(\ndef write_memory():\n    open("inbox.md", "w").write("unsafe")\n'
LOCAL_HELPER_BAD = '# memory-consumer\n\ndef check_boundary(*args, **kwargs):\n    return None\n\ndef write_memory():\n    check_boundary("raw_to_distilled", "safe")\n'
GOOD_LOCAL_IMPORT = '# memory-consumer\n\ndef write_memory():\n    from paulsha_hippo import policy\n    policy.check_boundary("raw_to_distilled", "safe", project_slug="_unknown", session_ref="s")\n'


def temporary_directory():
    return TemporaryDirectory(dir=TEMP_ROOT)


def load_lint_module():
    return importlib.import_module("paulsha_hippo.lint.policy_consumer_lint")


def run_lint(lint, *args: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return lint.main(list(args))


def run_lint_with_output(lint, *args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return lint.main(list(args)), stdout.getvalue(), stderr.getvalue()


class PolicyConsumerLintTests(unittest.TestCase):
    def test_lint_accepts_consumer_that_calls_check_boundary(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(GOOD, encoding="utf-8")
            self.assertEqual(run_lint(lint, str(root)), 0)

    def test_lint_accepts_function_local_policy_import(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(GOOD_LOCAL_IMPORT, encoding="utf-8")
            self.assertEqual(run_lint(lint, str(root)), 0)

    def test_lint_rejects_marker_consumer_without_check_boundary(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(BAD, encoding="utf-8")
            self.assertNotEqual(run_lint(lint, str(root)), 0)

    def test_lint_rejects_import_only_consumer_without_check_boundary(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(IMPORT_ONLY_BAD, encoding="utf-8")
            self.assertNotEqual(run_lint(lint, str(root)), 0)

    def test_lint_rejects_comment_only_boundary_reference(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(COMMENT_ONLY_BAD, encoding="utf-8")
            self.assertNotEqual(run_lint(lint, str(root)), 0)

    def test_lint_rejects_non_policy_check_boundary_helper(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text(LOCAL_HELPER_BAD, encoding="utf-8")
            self.assertNotEqual(run_lint(lint, str(root)), 0)

    def test_lint_ignores_policy_package_and_tests(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            policy_dir = root / "paulsha_hippo" / "policy"
            tests_dir = root / "tests"
            policy_dir.mkdir(parents=True)
            tests_dir.mkdir()
            (policy_dir / "consumer.py").write_text(BAD, encoding="utf-8")
            (tests_dir / "test_consumer.py").write_text(BAD, encoding="utf-8")
            self.assertEqual(run_lint(lint, str(root)), 0)

    def test_lint_detects_memory_path_string_consumer(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            (root / "consumer.py").write_text('def write_memory():\n    open("knowledge/note.md", "w").write("unsafe")\n', encoding="utf-8")
            self.assertNotEqual(run_lint(lint, str(root)), 0)

    def test_lint_rejects_non_utf8_file_without_traceback(self):
        lint = load_lint_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            candidate = root / "consumer.py"
            candidate.write_bytes(b"# memory-consumer\n\xff\n")

            returncode, stdout, stderr = run_lint_with_output(lint, str(root))

            self.assertNotEqual(returncode, 0)
            self.assertIn(str(candidate), stdout)
            self.assertIn("unable to decode as UTF-8", stdout)
            self.assertNotIn("Traceback", stdout)
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
