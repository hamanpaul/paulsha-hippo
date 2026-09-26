import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.importer.config import (
    ProjectConfig,
    ProjectsConfig,
    default_projects_path,
    load_projects_config,
)
from paulsha_hippo.importer.project_resolver import (
    ephemeral_roots,
    is_ephemeral_path,
    resolve_project,
)
from paulsha_hippo.importer.registry import load_union_projects_config, render_registry


REPO_ROOT = Path(__file__).resolve().parents[1]
_EMPTY = ProjectsConfig()
_SCRATCH_ROOT = REPO_ROOT.parent / ".test-work"
_SCRATCH_ROOT.mkdir(exist_ok=True)


def _init_repo(path: Path, remote: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


def _tempdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(dir=_SCRATCH_ROOT)


def _hermetic_git_env(**extra: str):
    """隔離 checkout 位置造成的假訊號（#117；docs/release-readiness.md 記載的巢狀 worktree 假失敗）。

    - GIT_CEILING_DIRECTORIES：git 向上搜尋不得越過 scratch root——repo 若 checkout 在另一個
      repo 內（如 `.worktrees/<name>`、`.claude/worktrees/*`），scratch 下的非 repo 目錄才不會
      誤中外層 repo 的 toplevel。
    - HIPPO_EPHEMERAL_ROOTS 預設清空：repo 若 checkout 在系統暫存目錄下（sandbox），scratch
      路徑不得因而被判為暫存 checkout；需要驗 ephemeral 行為的測試自行覆寫。
    """
    env = {"GIT_CEILING_DIRECTORIES": str(_SCRATCH_ROOT), "HIPPO_EPHEMERAL_ROOTS": ""}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=False)


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _make_repo_with_worktrees(base: Path, *, remote: str | None) -> tuple[Path, Path, Path]:
    """主 repo `widget/` + 兩種既有 worktree 慣例：`widget-worktrees/<branch>` 與 `widget/.worktrees/<name>`。"""
    main = base / "widget"
    main.mkdir()
    _init_repo(main, remote)
    _git("-C", str(main), "commit", "--allow-empty", "-m", "init")
    sibling = base / "widget-worktrees" / "feature-x"
    sibling.parent.mkdir()
    _git("-C", str(main), "worktree", "add", "-q", "-b", "feature-x", str(sibling))
    nested = main / ".worktrees" / "fix-y"
    _git("-C", str(main), "worktree", "add", "-q", "-b", "fix-y", str(nested))
    return main.resolve(), sibling.resolve(), nested.resolve()


class ProjectResolverTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def write_projects_config(self, text: str) -> Path:
        path = self.root / "projects.yaml"
        path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
        return path

    def test_repo_sample_config_contains_required_projects_and_aliases(self):
        sample = REPO_ROOT / "config" / "agents-projects.sample.yaml"

        config = load_projects_config(sample)

        self.assertIn("paulshaclaw", [project.slug for project in config.projects])
        self.assertIn("obs-auto-moc", [project.slug for project in config.projects])
        self.assertEqual(config.aliases["paulsha"], "paulshaclaw")
        self.assertEqual(config.aliases["obs-moc"], "obs-auto-moc")

    def test_default_projects_path_prefers_psc_config_root(self):
        with mock.patch.dict(os.environ, {"PSC_CONFIG_ROOT": "/tmp/psc-config-root"}, clear=False):
            self.assertEqual(
                str(default_projects_path(memory_root="/tmp/custom-memory")),
                "/tmp/psc-config-root/.agents/config/projects.yaml",
            )

    def test_resolve_project_uses_cwd_longest_prefix(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  repo:
                    roots:
                      - /workspace/repo
                  repo-tools:
                    roots:
                      - /workspace/repo-tools
                """
            )
        )

        project = resolve_project(cwd="/workspace/repo-tools/src/module", projects=config)

        self.assertEqual(project, "repo-tools")

    def test_resolve_project_prefers_nested_monorepo_child_over_parent(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  monorepo:
                    roots:
                      - /repo
                  monorepo-web:
                    roots:
                      - /repo/web
                """
            )
        )

        project = resolve_project(cwd="/repo/web/src", projects=config)

        self.assertEqual(project, "monorepo-web")

    def test_resolve_project_falls_back_to_explicit_git_toplevel(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  obs-auto-moc:
                    roots:
                      - /work/custom-claw-tools/obs-auto-moc
                """
            )
        )

        project = resolve_project(
            cwd="/worktrees/stage2-memory-importer-mvp",
            git_toplevel="/work/custom-claw-tools/obs-auto-moc",
            projects=config,
        )

        self.assertEqual(project, "obs-auto-moc")

    def test_resolve_project_maps_fallback_git_remote_to_slug(self):
        # payload carries no remote_url; the git fallback discovers the repo's
        # remote and MUST map it through projects.yaml to a slug, not return raw.
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  vendor-y:
                    remotes:
                      - internal-vcs.example/vendor-y/vendor-y_openwrt_feed
                """
            )
        )
        with mock.patch(
            "paulsha_hippo.importer.project_resolver._git.git_toplevel",
            remote_value="/srv/builder/PRJ-0611/vendor-mcu-cli",
        ), mock.patch(
            "paulsha_hippo.importer.project_resolver._git.git_remote",
            return_value="git@internal-vcs.example:vendor-y/vendor-y_openwrt_feed.git",
        ):
            project = resolve_project(
                cwd="/srv/worker/PROJ-0605/vendor-y-mcu-cleanup", projects=config
            )

        self.assertEqual(project, "vendor-y")

    def test_resolve_project_fallback_unregistered_remote_returns_normalized_url(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  vendor-y:
                    remotes:
                      - internal-vcs.example/vendor-y/vendor-y_openwrt_feed
                """
            )
        )
        with mock.patch(
            "paulsha_hippo.importer.project_resolver._git.git_toplevel",
            return_value="/some/unregistered/repo",
        ), mock.patch(
            "paulsha_hippo.importer.project_resolver._git.git_remote",
            return_value="git@github.com:other/thing.git",
        ):
            project = resolve_project(cwd="/some/unregistered/repo", projects=config)

        self.assertEqual(project, "github.com/other/thing")

    def test_resolve_project_matches_remote_url_when_paths_do_not_match(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            remote_url="git@github.com:hamanpaul/paulshaclaw.git",
            projects=config,
        )

        self.assertEqual(project, "paulshaclaw")

    def test_resolve_project_matches_remote_url_variants(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        for remote_url in (
            "https://github.com/hamanpaul/paulshaclaw.git/",
            "GitHub.com/hamanpaul/paulshaclaw",
            "https://token@github.com/hamanpaul/paulshaclaw.git",
            "ssh://git@github.com/hamanpaul/paulshaclaw.git",
            "ssh://git@github.com:22/hamanpaul/paulshaclaw.git",
        ):
            with self.subTest(remote_url=remote_url):
                project = resolve_project(
                    cwd="/unmatched/path",
                    git_toplevel="/another/unmatched/path",
                    remote_url=remote_url,
                    projects=config,
                )

                self.assertEqual(project, "paulshaclaw")

    def test_resolve_project_treats_malformed_remote_port_as_non_match(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="ssh://git@github.com:abc/hamanpaul/paulshaclaw.git",
            projects=config,
        )

        self.assertEqual(project, "path")

    def test_resolve_project_keeps_non_github_url_ports_distinct(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  example:
                    remotes:
                      - https://example.com:8443/org/repo.git
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="https://example.com:9443/org/repo.git",
            projects=config,
        )

        self.assertEqual(project, "path")

    def test_resolve_project_keeps_non_default_github_port_distinct(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="ssh://git@github.com:2222/hamanpaul/paulshaclaw.git",
            projects=config,
        )

        self.assertEqual(project, "path")

    def test_resolve_project_keeps_non_ssh_github_port_22_distinct(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="https://github.com:22/hamanpaul/paulshaclaw.git",
            projects=config,
        )

        self.assertEqual(project, "path")

    def test_resolve_project_preserves_file_remote_normalization(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  local-repo:
                    remotes:
                      - file:///repo/path.git
                """
            )
        )

        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="/repo/path.git",
            projects=config,
        )

        self.assertEqual(project, "local-repo")

    def test_resolve_project_returns_unknown_when_no_rule_matches(self):
        config = load_projects_config(
            self.write_projects_config(
                """
                version: 1
                projects:
                  paulshaclaw:
                    roots:
                      - /repo/paulshaclaw
                    remotes:
                      - github.com/hamanpaul/paulshaclaw
                """
            )
        )

        project = resolve_project(
            cwd="/elsewhere/project",
            git_toplevel="/elsewhere/project",
            remote_url="git@github.com:someone/else.git",
            projects=config,
        )

        self.assertEqual(project, "project")

    def test_alias_collision_warns_and_keeps_first_definition(self):
        config_path = self.write_projects_config(
            """
            version: 1
            projects:
              paulshaclaw:
                aliases: [shared, paulsha]
              obs-auto-moc:
                aliases: [shared, obs-moc]
            """
        )

        with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
            config = load_projects_config(config_path)

        self.assertEqual(config.aliases["shared"], "paulshaclaw")
        self.assertEqual(config.aliases["obs-moc"], "obs-auto-moc")
        self.assertIn("shared", "\n".join(captured.output))


class ResolveAutoDetectTests(unittest.TestCase):
    def setUp(self):
        self.env = _hermetic_git_env()
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_repo_with_remote_resolves_owner_repo(self):
        with _tempdir() as tmp:
            repo = Path(tmp) / "paulshaclaw"
            repo.mkdir()
            _init_repo(repo, "git@github.com:hamanpaul/paulshaclaw.git")

            self.assertEqual(resolve_project(cwd=str(repo), projects=_EMPTY), "github.com/hamanpaul/paulshaclaw")

    def test_repo_without_remote_resolves_dir_name(self):
        with _tempdir() as tmp:
            repo = Path(tmp) / "solo"
            repo.mkdir()
            _init_repo(repo)

            self.assertEqual(resolve_project(cwd=str(repo), projects=_EMPTY), "solo")

    def test_not_a_repo_resolves_working_folder(self):
        with _tempdir() as tmp:
            folder = Path(tmp) / "scratchpad"
            folder.mkdir()

            self.assertEqual(resolve_project(cwd=str(folder), projects=_EMPTY), "scratchpad")

    def test_multi_repo_workspace_resolves_tree_path(self):
        with _tempdir() as tmp:
            workspace = Path(tmp) / "work_prj"
            workspace.mkdir()
            repo_a = workspace / "serialwrap"
            repo_a.mkdir()
            _init_repo(repo_a)
            repo_b = workspace / "other"
            repo_b.mkdir()
            _init_repo(repo_b)

            self.assertEqual(resolve_project(cwd=str(repo_a), projects=_EMPTY), "work_prj/serialwrap")

    def test_truly_unresolvable_is_unknown(self):
        self.assertEqual(resolve_project(cwd=None, projects=_EMPTY), "_unknown")

    def test_root_and_dot_like_cwd_fall_back_to_unknown(self):
        with mock.patch(
            "paulsha_hippo.importer.project_resolver._git.git_toplevel",
            return_value=None,
        ):
            for cwd in ("/", "."):
                with self.subTest(cwd=cwd):
                    self.assertEqual(resolve_project(cwd=cwd, projects=_EMPTY), "_unknown")

    def test_git_detection_failure_degrades_to_folder_name(self):
        with _tempdir() as tmp:
            folder = Path(tmp) / "detached"
            folder.mkdir()

            with mock.patch(
                "paulsha_hippo.importer.project_resolver._git.git_toplevel",
                side_effect=OSError("git unavailable"),
            ):
                self.assertEqual(resolve_project(cwd=str(folder), projects=_EMPTY), "detached")

    def test_nonexistent_cwd_still_resolves_working_folder_name(self):
        with _tempdir() as tmp:
            cwd = Path(tmp) / "moved-folder"

            self.assertEqual(resolve_project(cwd=str(cwd), projects=_EMPTY), "moved-folder")

    def test_nonexistent_git_toplevel_falls_back_to_working_folder_name(self):
        with _tempdir() as tmp:
            cwd = Path(tmp) / "scratchpad"
            cwd.mkdir()
            git_toplevel = Path(tmp) / "moved" / "ghost-repo"

            self.assertEqual(
                resolve_project(cwd=str(cwd), git_toplevel=str(git_toplevel), projects=_EMPTY),
                "scratchpad",
            )

    def test_path_like_remote_url_does_not_override_repo_name(self):
        with _tempdir() as tmp:
            repo = Path(tmp) / "solo"
            repo.mkdir()
            _init_repo(repo)

            self.assertEqual(resolve_project(cwd=str(repo), remote_url="/tmp/ws/repo", projects=_EMPTY), "solo")


class WorktreeConvergenceTests(unittest.TestCase):
    """#117 驗收：主 root、`<repo>-worktrees/<branch>`、`.worktrees/<name>` 三種 cwd 解析出同一 slug。"""

    REMOTE = "git@github.com:acme/widget.git"

    def setUp(self):
        self.env = _hermetic_git_env()
        self.env.start()
        self.tmp = _tempdir()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        self.env.stop()

    def assert_converges(self, cwds, projects, expected):
        for cwd in cwds:
            with self.subTest(cwd=str(cwd)):
                self.assertEqual(resolve_project(cwd=str(cwd), projects=projects), expected)

    def test_roots_only_registry_converges_worktrees_to_registered_slug(self):
        # 根因重現：registry 只有 roots、沒有 remotes 時，sibling worktree 不在 root 前綴下，
        # 舊解析落到 remote fallback 回傳 raw remote 字串，knowledge bucket 因而碎裂。
        main, sibling, nested = _make_repo_with_worktrees(self.base, remote=self.REMOTE)
        (main / "src").mkdir()
        (sibling / "src").mkdir()
        projects = ProjectsConfig(projects=(ProjectConfig(slug="widget", roots=(str(main),)),))

        self.assert_converges((main, main / "src", sibling, sibling / "src", nested), projects, "widget")

    def test_registry_with_remotes_converges_worktrees_to_registered_slug(self):
        main, sibling, nested = _make_repo_with_worktrees(self.base, remote=self.REMOTE)
        projects = ProjectsConfig(
            projects=(
                ProjectConfig(slug="widget", roots=(str(main),), remotes=("github.com/acme/widget",)),
            )
        )

        self.assert_converges((main, sibling, nested), projects, "widget")

    def test_unregistered_repo_with_remote_converges_to_raw_remote(self):
        main, sibling, nested = _make_repo_with_worktrees(self.base, remote=self.REMOTE)

        self.assert_converges((main, sibling, nested), _EMPTY, "github.com/acme/widget")

    def test_unregistered_remoteless_repo_converges_to_main_repo_name(self):
        # 無 remote、未登記：worktree 的目錄名 fallback 必須歸併到主 repo 目錄名，
        # 不得各自以 `feature-x`／`fix-y` 產生新 bucket。
        main, sibling, nested = _make_repo_with_worktrees(self.base, remote=None)

        expected = resolve_project(cwd=str(main), projects=_EMPTY)
        self.assertEqual(expected, "widget")
        self.assert_converges((sibling, nested), _EMPTY, expected)

    def test_registered_root_match_still_wins_over_remote_mapping(self):
        # root 登記（較具體的本機事實）優先於 remote 對應，與既有步驟 1/2 的優先序一致。
        main, sibling, _nested = _make_repo_with_worktrees(self.base, remote=self.REMOTE)
        projects = ProjectsConfig(
            projects=(
                ProjectConfig(slug="widget", roots=(str(main),)),
                ProjectConfig(slug="widget-mirror", remotes=("github.com/acme/widget",)),
            )
        )

        self.assert_converges((main, sibling), projects, "widget")


class EphemeralCheckoutTests(unittest.TestCase):
    """#117：暫存目錄下無 remote 的 checkout 歸 `_unknown`，不得以目錄名產生假 project。"""

    def setUp(self):
        self.tmp = _tempdir()
        self.base = Path(self.tmp.name)
        self.fake_tmp = self.base / "fake-system-tmp"
        self.fake_tmp.mkdir()
        self.env = _hermetic_git_env(HIPPO_EPHEMERAL_ROOTS=str(self.fake_tmp))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_planning_sandbox_copy_without_git_is_unknown(self):
        checkout = self.fake_tmp / "cortex-planning-abc123" / "checkout"
        checkout.mkdir(parents=True)

        self.assertEqual(resolve_project(cwd=str(checkout), projects=_EMPTY), "_unknown")

    def test_remoteless_repo_under_ephemeral_root_is_unknown(self):
        checkout = self.fake_tmp / "sandbox-1" / "checkout"
        checkout.mkdir(parents=True)
        _init_repo(checkout)

        self.assertEqual(resolve_project(cwd=str(checkout), projects=_EMPTY), "_unknown")

    def test_ephemeral_root_itself_is_unknown(self):
        self.assertEqual(resolve_project(cwd=str(self.fake_tmp), projects=_EMPTY), "_unknown")

    def test_deleted_ephemeral_cwd_is_unknown(self):
        gone = self.fake_tmp / "cortex-480-canary-XyZ"

        self.assertEqual(resolve_project(cwd=str(gone), projects=_EMPTY), "_unknown")

    def test_repo_with_remote_under_ephemeral_root_keeps_remote_identity(self):
        checkout = self.fake_tmp / "clone" / "widget"
        checkout.mkdir(parents=True)
        _init_repo(checkout, "git@github.com:acme/widget.git")

        self.assertEqual(resolve_project(cwd=str(checkout), projects=_EMPTY), "github.com/acme/widget")

    def test_registered_root_under_ephemeral_root_keeps_registered_slug(self):
        checkout = self.fake_tmp / "pinned" / "checkout"
        checkout.mkdir(parents=True)
        projects = ProjectsConfig(projects=(ProjectConfig(slug="pinned", roots=(str(checkout),)),))

        self.assertEqual(resolve_project(cwd=str(checkout), projects=projects), "pinned")

    def test_non_ephemeral_folder_keeps_folder_name_fallback(self):
        folder = self.base / "notes"
        folder.mkdir()

        self.assertEqual(resolve_project(cwd=str(folder), projects=_EMPTY), "notes")


class EphemeralRootsConfigTests(unittest.TestCase):
    def test_default_roots_include_system_tmp(self):
        env = {key: value for key, value in os.environ.items() if key != "HIPPO_EPHEMERAL_ROOTS"}
        # HOME 若恰在 /tmp 之下（部分 sandbox），/tmp 會依「HOME 祖先排除」規則剔除；固定合成 HOME
        env["HOME"] = "/nonexistent-home/tester"
        with mock.patch.dict(os.environ, env, clear=True):
            roots = ephemeral_roots()
        self.assertIn(os.path.realpath("/tmp"), roots)

    def test_env_override_replaces_defaults_and_empty_disables(self):
        with mock.patch.dict(os.environ, {"HIPPO_EPHEMERAL_ROOTS": "/srv/sandboxes"}, clear=False):
            self.assertEqual(ephemeral_roots(), (os.path.realpath("/srv/sandboxes"),))
        with mock.patch.dict(os.environ, {"HIPPO_EPHEMERAL_ROOTS": ""}, clear=False):
            self.assertEqual(ephemeral_roots(), ())

    def test_filesystem_root_and_relative_entries_are_ignored(self):
        value = os.pathsep.join(["/", "relative/dir", "/srv/sandboxes"])
        with mock.patch.dict(os.environ, {"HIPPO_EPHEMERAL_ROOTS": value}, clear=False):
            self.assertEqual(ephemeral_roots(), (os.path.realpath("/srv/sandboxes"),))

    def test_home_and_its_ancestors_are_never_ephemeral(self):
        home = Path.home()
        value = os.pathsep.join([str(home), str(home.parent)])
        with mock.patch.dict(os.environ, {"HIPPO_EPHEMERAL_ROOTS": value}, clear=False):
            self.assertEqual(ephemeral_roots(), ())

    def test_is_ephemeral_path_matches_root_and_descendants_only(self):
        with mock.patch.dict(os.environ, {"HIPPO_EPHEMERAL_ROOTS": "/srv/sandboxes"}, clear=False):
            self.assertTrue(is_ephemeral_path("/srv/sandboxes"))
            self.assertTrue(is_ephemeral_path("/srv/sandboxes/a/checkout"))
            self.assertFalse(is_ephemeral_path("/srv/sandboxes-other/checkout"))
            self.assertFalse(is_ephemeral_path(None))


class UnionReadTests(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def write_projects_config(self, text: str) -> Path:
        path = self.root / "projects.yaml"
        path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
        return path

    def write_registry(self, projects) -> Path:
        path = self.root / "config" / "paulsha" / "project-hippo.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_registry(projects), encoding="utf-8")
        return path

    def test_union_adds_registry_only_projects(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              manual-proj:
                remotes:
                  - github.com/acme/manual
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", remotes=("github.com/acme/widget",)),)
        )
        config = load_union_projects_config(legacy, registry_path)
        self.assertEqual([project.slug for project in config.projects], ["manual-proj", "widget"])

    def test_union_merges_same_slug_manual_first(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              widget:
                roots:
                  - /data/manual-root
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", roots=("/data/discovered-root",), remotes=("github.com/acme/widget",)),)
        )
        config = load_union_projects_config(legacy, registry_path)
        self.assertEqual(len(config.projects), 1)
        self.assertEqual(config.projects[0].roots, ("/data/manual-root", "/data/discovered-root"))
        self.assertEqual(config.projects[0].remotes, ("github.com/acme/widget",))

    def test_union_merged_path_preserves_legacy_families(self):
        legacy = self.write_projects_config(
            """
            version: 1
            families:
              - [MCU-Octopus, ot-ti-mirror]
            projects:
              manual-proj:
                remotes:
                  - github.com/acme/manual
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", remotes=("github.com/acme/widget",)),)
        )
        config = load_union_projects_config(legacy, registry_path)
        self.assertEqual([project.slug for project in config.projects], ["manual-proj", "widget"])
        self.assertEqual(config.families, (("MCU-Octopus", "ot-ti-mirror"),))

    def test_alias_collision_manual_wins_with_warning(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              manual-proj:
                aliases: [shared]
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="generated-proj", aliases=("shared",)),)
        )
        with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
            config = load_union_projects_config(legacy, registry_path)
        self.assertEqual(config.aliases["shared"], "manual-proj")
        self.assertIn("shared", "\n".join(captured.output))

    def test_missing_registry_keeps_legacy_behavior(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              paulshaclaw:
                remotes:
                  - github.com/hamanpaul/paulshaclaw
            """
        )
        config = load_union_projects_config(legacy, self.root / "absent" / "project-hippo.yaml")
        self.assertEqual([project.slug for project in config.projects], ["paulshaclaw"])

    def test_resolve_project_reads_registry_remote_by_default_load(self):
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", remotes=("github.com/acme/widget",)),)
        )
        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="git@github.com:acme/widget.git",
            config_path=str(self.root / "absent-projects.yaml"),
            registry_path=str(registry_path),
        )
        self.assertEqual(project, "widget")

    def test_resolve_project_reads_registry_roots_by_default_load(self):
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", roots=("/data/widget",)),)
        )
        project = resolve_project(
            cwd="/data/widget/src/module",
            config_path=str(self.root / "absent-projects.yaml"),
            registry_path=str(registry_path),
        )
        self.assertEqual(project, "widget")


if __name__ == "__main__":
    unittest.main()
