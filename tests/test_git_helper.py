from __future__ import annotations
import os, subprocess, unittest
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

    def test_git_main_toplevel_worktree_resolves_main_root(self) -> None:
        with TemporaryDirectory() as tmp:
            main = Path(tmp) / "mainrepo"
            main.mkdir()
            _init_repo(main)
            subprocess.run(
                ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "--allow-empty", "-m", "init"],
                check=True, capture_output=True,
            )
            worktree = Path(tmp) / "wt"
            subprocess.run(
                ["git", "-C", str(main), "worktree", "add", "-b", "wt-branch", str(worktree)],
                check=True, capture_output=True,
            )
            wt_top = _git.git_toplevel(str(worktree))
            self.assertEqual(Path(wt_top).resolve(), worktree.resolve())
            main_top = _git.git_main_toplevel(wt_top)
            self.assertEqual(Path(main_top).resolve(), main.resolve())

    def test_git_main_toplevel_normal_checkout_returns_itself(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "solo"
            repo.mkdir()
            _init_repo(repo)
            top = _git.git_toplevel(str(repo))
            self.assertEqual(Path(_git.git_main_toplevel(top)).resolve(), repo.resolve())

    def test_git_main_toplevel_falsy_returns_none(self) -> None:
        self.assertIsNone(_git.git_main_toplevel(None))
        self.assertIsNone(_git.git_main_toplevel(""))

    def test_git_main_toplevel_non_repo_falls_back_to_input(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(_git.git_main_toplevel(tmp), str(tmp))

    def test_git_head_returns_sha_or_none(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"; repo.mkdir(); _init_repo(repo)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            head = _git.git_head(str(repo))
            self.assertRegex(head, r"^[0-9a-f]{40}$")
            self.assertIsNone(_git.git_head(None))
            self.assertIsNone(_git.git_head(tmp))  # 非 repo

    def test_rev_before_returns_approx_commit_for_timestamp(self) -> None:
        # 真 repo、兩個帶明確 commit 日期的 commit（issue #136 fix 1b/1c backfill 用）：
        # rev-list --before= 近似值須落在正確的一側。
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"; repo.mkdir(); _init_repo(repo)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

            def _commit(msg: str, when: str) -> str:
                run_env = {**env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
                subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                                check=True, env=run_env)
                return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       check=True, capture_output=True, text=True).stdout.strip()

            c1 = _commit("c1", "2026-08-01T00:00:00+00:00")
            c2 = _commit("c2", "2026-08-10T00:00:00+00:00")

            self.assertEqual(_git.git_rev_before(str(repo), "2026-08-05T00:00:00+00:00"), c1)
            self.assertEqual(_git.git_rev_before(str(repo), "2026-08-31T00:00:00+00:00"), c2)
            self.assertIsNone(_git.git_rev_before(str(repo), "2025-01-01T00:00:00+00:00"))
            self.assertIsNone(_git.git_rev_before(None, "2026-08-05T00:00:00+00:00"))
            self.assertIsNone(_git.git_rev_before(str(repo), None))
