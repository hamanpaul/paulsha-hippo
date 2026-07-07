from __future__ import annotations
import subprocess, unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from paulsha_hippo.importer import _git


def _init_repo(path: Path, remote: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


class GitHelperTests(unittest.TestCase):
    def test_toplevel_and_remote(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            _init_repo(repo, "git@github.com:owner/myrepo.git")
            top = _git.git_toplevel(str(repo))
            self.assertEqual(Path(top).resolve(), repo.resolve())
            self.assertEqual(_git.git_remote(top), "git@github.com:owner/myrepo.git")

    def test_non_repo_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(_git.git_toplevel(tmp))

    def test_sibling_repo_count(self) -> None:
        with TemporaryDirectory() as tmp:
            for name in ("a", "b", "plain"):
                d = Path(tmp) / name
                d.mkdir()
            _init_repo(Path(tmp) / "a")
            _init_repo(Path(tmp) / "b")
            self.assertEqual(_git.sibling_repo_count(str(Path(tmp) / "a")), 2)

    def test_falsy_inputs(self) -> None:
        # None or empty inputs must be treated as non-repo / safe fallback
        self.assertIsNone(_git.git_toplevel(None))
        self.assertIsNone(_git.git_remote(None))
        self.assertEqual(_git.sibling_repo_count(''), 0)

    def test_nonexistent_path_returns_zero(self) -> None:
        # Non-existent path should not scan siblings; must return 0
        with TemporaryDirectory() as tmp:
            # create a real sibling repo so current implementation would count it
            repo = Path(tmp) / "a"
            repo.mkdir()
            _init_repo(repo)
            missing = Path(tmp) / "nope"
            self.assertEqual(_git.sibling_repo_count(str(missing)), 0)

    def test_nested_outer_repo_does_not_inherit(self) -> None:
        # An outer repo should not cause plain sibling dirs to be counted.
        with TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            # init outer repo
            _init_repo(outer)
            workspace = outer / "workspace"
            workspace.mkdir()
            a = workspace / "a"
            plain = workspace / "plain"
            a.mkdir()
            plain.mkdir()
            # init a as its own git repo (so it has its own .git)
            _init_repo(a)

            # sibling_repo_count for 'a' should only count actual sibling repos (none), so 1 (a itself?)
            # The function counts siblings in the same parent, so 'a' has one sibling 'plain' which is not a repo.
            self.assertEqual(_git.sibling_repo_count(str(a)), 1)
