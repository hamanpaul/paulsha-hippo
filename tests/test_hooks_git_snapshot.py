# tests/test_hooks_git_snapshot.py
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "paulsha_hippo" / "hooks"
CAPTURE_HOOKS = ("claude_session_end.py", "codex_session_end.py", "copilot_session_end.py")


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    (path / "dirty.txt").write_text("x", encoding="utf-8")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _run(hook: str, payload: dict, memory_root: Path, path_env: str = "/usr/bin:/bin") -> Path:
    env = {"PATH": path_env, "HOME": str(memory_root.parent), "PSC_MEMORY_ROOT": str(memory_root),
           "PSC_IMPORTER_DISABLED": "1"}
    p = subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, cwd=str(memory_root.parent))
    assert p.returncode == 0, p.stderr
    queue = list((memory_root / "runtime" / "queue").glob("*.json"))
    assert len(queue) == 1, queue
    return queue[0]


def test_hooks_fill_git_snapshot_from_cwd(tmp_path):
    head = _git_repo(tmp_path / "repo")
    for hook in CAPTURE_HOOKS:
        mr = tmp_path / hook.replace(".py", "") / "memory"
        mr.mkdir(parents=True)
        written = json.loads(_run(hook, {"session_id": "s1", "cwd": str(tmp_path / "repo")}, mr).read_text())
        assert written["commit"] == head, hook
        assert written["git_branch"] in ("master", "main"), hook
        assert written["git_dirty"] is True, hook


def test_hook_keeps_payload_commit_when_present(tmp_path):
    _git_repo(tmp_path / "repo")
    mr = tmp_path / "memory"; mr.mkdir()
    written = json.loads(_run("claude_session_end.py",
                              {"session_id": "s1", "cwd": str(tmp_path / "repo"), "commit": "e300b08"}, mr).read_text())
    assert written["commit"] == "e300b08"


def test_hook_non_repo_and_missing_git_leave_keys_absent(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    mr1 = tmp_path / "m1"; mr1.mkdir()
    written = json.loads(_run("claude_session_end.py", {"session_id": "s1", "cwd": str(plain)}, mr1).read_text())
    assert "commit" not in written and "git_branch" not in written and "git_dirty" not in written
    _git_repo(tmp_path / "repo")
    mr2 = tmp_path / "m2"; mr2.mkdir()
    # PATH 只剩一個空目錄：找不到 git → 仍 exit 0、queue 寫入、無 git 鍵
    empty_bin = tmp_path / "emptybin"; empty_bin.mkdir()
    written = json.loads(_run("claude_session_end.py", {"session_id": "s2", "cwd": str(tmp_path / "repo")},
                              mr2, path_env=str(empty_bin)).read_text())
    assert "commit" not in written
