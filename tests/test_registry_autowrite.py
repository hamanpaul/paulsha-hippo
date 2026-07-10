import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.importer.pipeline import ingest_queue_item
from paulsha_hippo.importer.registry import parse_registry


REPO_ROOT = Path(__file__).resolve().parents[1]


class RegistryAutoWriteTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.base = Path(self.tmp.name)
        self.memory_root = self.base / "agents" / "memory"
        self.queue = self.memory_root / "runtime" / "queue"
        self.queue.mkdir(parents=True)
        self.hippo_config = self.base / "hippo-config"
        self.hippo_config.mkdir()
        self.env = mock.patch.dict(
            os.environ, {"HIPPO_CONFIG_ROOT": str(self.hippo_config)}, clear=False
        )
        self.env.start()
        os.environ.pop("PSC_CONFIG_ROOT", None)
        # project_registry_path(memory_root) = <memory_root 上一層>/config/paulsha/project-hippo.yaml
        self.registry_path = self.base / "agents" / "config" / "paulsha" / "project-hippo.yaml"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def enable_auto_write(self):
        (self.hippo_config / "config.yaml").write_text(
            "project_registry:\n  auto_write: true\n", encoding="utf-8"
        )

    def make_repo(self, name="widget", remote="git@github.com:acme/widget.git"):
        repo = self.base / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        if remote:
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", remote], check=True
            )
        return repo

    def payload(self, *, cwd, session_id="registry-sid-001", remote_url=None):
        data = {
            "tool": "copilot-cli",
            "session_id": session_id,
            "capture_scope": "session_end",
            "ended_at": "2026-07-10T10:00:00+00:00",
            "cwd": str(cwd),
            "repo": "",
            "commit": "",
            "turn_count": 2,
            "user_prompts": ["implement registry"],
            "assistant_summary": "summary",
            "touched_files": ["src/registry.py"],
            "referenced_artifacts": [],
        }
        if remote_url is not None:
            data["remote_url"] = remote_url
        return data

    def ingest(self, payload, name="item.json", **kwargs):
        path = self.queue / name
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return ingest_queue_item(path, memory_root=self.memory_root, **kwargs)

    def read_projects(self):
        return parse_registry(self.registry_path.read_text(encoding="utf-8"))

    def test_auto_write_default_off(self):
        repo = self.make_repo(name="offrepo", remote="git@github.com:acme/offrepo.git")
        decision = self.ingest(self.payload(cwd=repo, session_id="registry-sid-off"))
        self.assertEqual(decision["status"], "written")
        self.assertIn("discovery", decision)
        self.assertFalse(self.registry_path.exists())

    def test_auto_write_records_discovery_and_is_idempotent(self):
        self.enable_auto_write()
        repo = self.make_repo()
        decision = self.ingest(self.payload(cwd=repo), name="a.json")
        self.assertEqual(decision["status"], "written")
        projects = self.read_projects()
        self.assertEqual([project.slug for project in projects], ["github.com/acme/widget"])
        self.assertEqual(projects[0].remotes, ("github.com/acme/widget",))
        self.assertEqual(len(projects[0].roots), 1)
        self.assertEqual(Path(projects[0].roots[0]).resolve(), repo.resolve())
        before = self.registry_path.read_bytes()
        second = self.ingest(self.payload(cwd=repo), name="b.json")
        self.assertEqual(second["status"], "hash-duplicate")
        self.assertEqual(self.registry_path.read_bytes(), before)
        ledger = self.memory_root / "runtime" / "ledger" / "import.jsonl"
        for line in ledger.read_text(encoding="utf-8").splitlines():
            self.assertNotIn('"discovery"', line)

    def test_multi_remote_normalizes_and_dedupes(self):
        self.enable_auto_write()
        repo = self.make_repo()
        self.ingest(
            self.payload(
                cwd=repo,
                session_id="registry-sid-multi",
                remote_url="https://x-access-token@github.com/ACME/widget.git",
            )
        )
        projects = self.read_projects()
        self.assertEqual(projects[0].remotes, ("github.com/acme/widget",))

    def test_path_shaped_payload_repo_not_persisted_as_remote(self):
        # 對齊 test_idempotency 的既有紀律：path 形 payload['repo'] 是 toplevel 路徑輸入
        # （僅供 resolve_project 比對），不得被 normalize_remote 變造成假 remote
        # （work/...）持久化進 registry remotes（spec §3.3.2、契約 §2/§3）。
        self.enable_auto_write()
        repo = self.make_repo(name="pathrepo", remote="git@github.com:acme/pathrepo.git")
        payload = self.payload(cwd=repo, session_id="registry-sid-pathshape")
        payload["repo"] = "/work/custom-claw-tools/obs-auto-moc"
        decision = self.ingest(payload, name="pathshape.json")
        self.assertEqual(decision["status"], "written")
        projects = self.read_projects()
        all_remotes = {remote for project in projects for remote in project.remotes}
        self.assertEqual(all_remotes, {"github.com/acme/pathrepo"})
        self.assertNotIn("work/custom-claw-tools/obs-auto-moc", all_remotes)

    def test_worktree_cwd_registers_main_root(self):
        self.enable_auto_write()
        repo = self.make_repo(name="mainrepo", remote="git@github.com:acme/mainrepo.git")
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
             "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        worktree = self.base / "wt"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "wt-branch", str(worktree)],
            check=True, capture_output=True,
        )
        self.ingest(self.payload(cwd=worktree, session_id="registry-sid-wt"))
        projects = self.read_projects()
        self.assertEqual(len(projects[0].roots), 1)
        self.assertEqual(Path(projects[0].roots[0]).resolve(), repo.resolve())

    def test_registry_failure_does_not_break_ingest(self):
        self.enable_auto_write()
        repo = self.make_repo(name="failrepo", remote="git@github.com:acme/failrepo.git")
        with mock.patch(
            "paulsha_hippo.importer.registry.record_discovery",
            side_effect=OSError("disk full"),
        ):
            with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
                decision = self.ingest(self.payload(cwd=repo, session_id="registry-sid-fail"))
        self.assertEqual(decision["status"], "written")
        self.assertTrue(Path(decision["inbox_path"]).exists())
        self.assertIn("fail-open", "\n".join(captured.output))

    def test_non_repo_cwd_without_remote_not_recorded(self):
        self.enable_auto_write()
        # 註：plain folder 必須位於 repo 之外——.test-work 在 hippo worktree 內，
        # git 向上搜尋會誤中 hippo repo 本身（比照 test_git_helper 的系統暫存目錄慣例）。
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "plain-folder"
            folder.mkdir()
            decision = self.ingest(self.payload(cwd=folder, session_id="registry-sid-plain"))
        self.assertEqual(decision["status"], "written")
        self.assertFalse(self.registry_path.exists())

    def test_dry_run_does_not_write_registry(self):
        self.enable_auto_write()
        repo = self.make_repo(name="dryrepo", remote="git@github.com:acme/dryrepo.git")
        decision = self.ingest(
            self.payload(cwd=repo, session_id="registry-sid-dry"), name="dry.json", dry_run=True
        )
        self.assertIn("discovery", decision)
        self.assertFalse(self.registry_path.exists())


if __name__ == "__main__":
    unittest.main()
