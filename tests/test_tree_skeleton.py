import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class MemoryTreeSkeletonTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def test_tree_only_install_creates_private_memory_tree(self):
        root = Path(self.tmp.name) / "memory"
        completed = subprocess.run(
            [
                "bash",
                "paulsha_hippo/hooks/install.sh",
                "--tree-only",
                "--memory-root",
                str(root),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        for relative in [
            "inbox",
            "work-centric",
            "knowledge",
            "runtime",
            "log",
            "hooks",
            "archive",
            "inbox/sessions",
            "inbox/plans",
            "inbox/research",
            "inbox/reports",
            "work-centric/common-sense",
            "runtime/queue",
            "runtime/queue/_failed",
            "runtime/locks",
            "runtime/ledger",
            "runtime/indexes",
            "archive/queue",
        ]:
            path = root / relative
            self.assertTrue(path.is_dir(), relative)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertTrue((path / ".gitkeep").exists(), relative)

    def test_tree_only_rejects_empty_memory_root(self):
        completed = subprocess.run(
            [
                "bash",
                "paulsha_hippo/hooks/install.sh",
                "--tree-only",
                "--memory-root",
                "",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--memory-root must not be empty", completed.stderr)

    def test_tree_only_rejects_whitespace_only_memory_root(self):
        completed = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "paulsha_hippo/hooks/install.sh"),
                "--tree-only",
                "--memory-root",
                "   ",
            ],
            cwd=self.tmp.name,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--memory-root must not be empty", completed.stderr)

    def test_tree_only_rejects_filesystem_root_memory_root(self):
        completed = subprocess.run(
            [
                "bash",
                "paulsha_hippo/hooks/install.sh",
                "--tree-only",
                "--memory-root",
                "/",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--memory-root must not be /", completed.stderr)

    def test_tree_only_rejects_slash_only_memory_root(self):
        completed = subprocess.run(
            [
                "bash",
                "paulsha_hippo/hooks/install.sh",
                "--tree-only",
                "--memory-root",
                "//",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--memory-root must not be /", completed.stderr)


if __name__ == "__main__":
    unittest.main()
