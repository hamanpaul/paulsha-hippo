"""#117：既有 registry 的 remotes 一次性 backfill（`hippo registry backfill-remotes`）。

全部使用合成路徑（tmp_path）與合成 remote；不讀寫使用者真實 registry。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from paulsha_hippo import cli
from paulsha_hippo.importer.config import ProjectConfig
from paulsha_hippo.importer.project_resolver import resolve_project
from paulsha_hippo.importer.registry import parse_registry, render_registry

HANDWRITTEN_REGISTRY = "projects:\n  - slug: widget\n    roots:\n      - {root}\n"


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _repo(path: Path, remote: str | None = "git@github.com:acme/widget.git") -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", str(path))
    if remote:
        _git("-C", str(path), "remote", "add", "origin", remote)
    return path.resolve()


def _backups(registry: Path) -> list[Path]:
    return sorted(registry.parent.glob(f"{registry.name}.bak-117-*"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("PSC_CONFIG_ROOT", raising=False)
    config = tmp_path / "agents" / "config"
    registry = config / "paulsha" / "project-hippo.yaml"
    legacy = config / "projects.yaml"
    registry.parent.mkdir(parents=True)
    return {"base": tmp_path, "registry": registry, "legacy": legacy}


def _run(env, *extra: str, capsys) -> tuple[int, dict]:
    code = cli.main(
        [
            "registry",
            "backfill-remotes",
            "--registry",
            str(env["registry"]),
            "--projects",
            str(env["legacy"]),
            *extra,
        ]
    )
    out = capsys.readouterr().out
    return code, json.loads(out)


def _status_by_slug(report: dict) -> dict[str, str]:
    return {item["slug"]: item["status"] for item in report["items"]}


def test_dry_run_is_default_and_never_writes(env, capsys):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    before = env["registry"].read_bytes()

    code, report = _run(env, capsys=capsys)

    assert code == 0
    assert report["mode"] == "dry-run"
    assert report["items"] == [
        {
            "slug": "widget",
            "root": str(repo),
            "status": "add",
            "remote": "github.com/acme/widget",
            "claimed_by": [],
        }
    ]
    assert report["summary"] == {"add": 1}
    assert report["changed"] is False
    assert env["registry"].read_bytes() == before
    assert _backups(env["registry"]) == []


def test_apply_writes_remote_backs_up_first_and_is_idempotent(env, capsys):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    original = env["registry"].read_bytes()

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 0
    assert report["mode"] == "apply"
    assert report["changed"] is True
    projects = parse_registry(env["registry"].read_text(encoding="utf-8"))
    assert [(p.slug, p.roots, p.remotes) for p in projects] == [
        ("widget", (str(repo),), ("github.com/acme/widget",))
    ]
    backups = _backups(env["registry"])
    assert [str(path) for path in backups] == [report["backup"]]
    assert backups[0].read_bytes() == original
    assert report["restore"].startswith("cp -p ")

    after = env["registry"].read_bytes()
    code, rerun = _run(env, "--apply", capsys=capsys)
    assert code == 0
    assert rerun["changed"] is False
    assert _status_by_slug(rerun) == {"widget": "present"}
    assert env["registry"].read_bytes() == after
    assert len(_backups(env["registry"])) == 1


def test_restore_command_recovers_original_bytes(env, capsys):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(HANDWRITTEN_REGISTRY.format(root=repo), encoding="utf-8")
    original = env["registry"].read_bytes()

    _code, report = _run(env, "--apply", capsys=capsys)
    assert env["registry"].read_bytes() != original
    subprocess.run(report["restore"], shell=True, check=True)

    assert env["registry"].read_bytes() == original


def test_handwritten_registry_without_schema_is_canonicalized(env, capsys):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(HANDWRITTEN_REGISTRY.format(root=repo), encoding="utf-8")

    _run(env, "--apply", capsys=capsys)

    text = env["registry"].read_text(encoding="utf-8")
    assert text.startswith("# GENERATED")
    assert "schema_version: 1" in text
    projects = parse_registry(text)
    assert projects[0].remotes == ("github.com/acme/widget",)


def test_legacy_only_project_is_backfilled_into_generated_registry(env, capsys):
    repo = _repo(env["base"] / "legacy-proj", remote="https://github.com/Acme/Legacy-Proj.git")
    env["legacy"].write_text(
        f"projects:\n  legacy-proj:\n    roots:\n      - {repo}\n", encoding="utf-8"
    )
    legacy_before = env["legacy"].read_bytes()

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 0
    projects = parse_registry(env["registry"].read_text(encoding="utf-8"))
    assert [(p.slug, p.remotes) for p in projects] == [("legacy-proj", ("github.com/acme/legacy-proj",))]
    assert env["legacy"].read_bytes() == legacy_before  # manual 檔不改寫
    assert report["backup"] is None
    assert report["restore"].startswith("rm ")


def test_unsafe_candidates_are_reported_and_never_written(env, capsys):
    widget = _repo(env["base"] / "widget")
    twin_a = _repo(env["base"] / "twin-a", remote="git@github.com:acme/twin.git")
    twin_b = _repo(env["base"] / "twin-b", remote="git@github.com:acme/twin.git")
    solo = _repo(env["base"] / "solo", remote=None)
    workspace = env["base"] / "workspace"
    workspace.mkdir()
    (widget / "sub").mkdir()
    env["legacy"].write_text(
        "projects:\n"
        "  other:\n"
        "    roots:\n"
        "      - /path/to/placeholder\n"
        "    remotes:\n"
        "      - github.com/acme/widget\n",
        encoding="utf-8",
    )
    env["registry"].write_text(
        render_registry(
            (
                ProjectConfig(slug="widget", roots=(str(widget),)),
                ProjectConfig(slug="twin-a", roots=(str(twin_a),)),
                ProjectConfig(slug="twin-b", roots=(str(twin_b),)),
                ProjectConfig(slug="solo", roots=(str(solo),)),
                ProjectConfig(slug="ws", roots=(str(workspace),)),
                ProjectConfig(slug="sub", roots=(str(widget / "sub"),)),
            )
        ),
        encoding="utf-8",
    )
    before = env["registry"].read_bytes()

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 0
    assert _status_by_slug(report) == {
        "other": "root-missing",
        "widget": "conflict",
        "twin-a": "conflict",
        "twin-b": "conflict",
        "solo": "no-remote",
        "ws": "not-a-repo",
        "sub": "not-repo-root",
    }
    claimed = {item["slug"]: item["claimed_by"] for item in report["items"]}
    assert claimed["widget"] == ["other"]
    assert claimed["twin-a"] == ["twin-b"]
    assert report["changed"] is False
    assert env["registry"].read_bytes() == before
    assert _backups(env["registry"]) == []


def test_backfilled_registry_converges_sibling_worktree_by_remote(env, capsys, monkeypatch):
    # 驗收：補上 remotes 後，不在 root 前綴下的 worktree 也經 remote 收斂回既有 slug。
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(env["base"]))
    repo = _repo(env["base"] / "widget")
    _git("-C", str(repo), "commit", "--allow-empty", "-m", "init")
    worktree = env["base"] / "elsewhere" / "feature-x"
    _git("-C", str(repo), "worktree", "add", "-q", "-b", "feature-x", str(worktree))
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    _run(env, "--apply", capsys=capsys)

    # 以「無 roots、只剩 remotes」的 registry 驗證 remote 路徑本身即可收斂
    projects = parse_registry(env["registry"].read_text(encoding="utf-8"))
    remote_only = env["base"] / "remote-only.yaml"
    remote_only.write_text(
        render_registry(tuple(ProjectConfig(slug=p.slug, remotes=p.remotes) for p in projects)),
        encoding="utf-8",
    )
    for registry_path in (env["registry"], remote_only):
        assert (
            resolve_project(
                cwd=str(worktree),
                config_path=str(env["legacy"]),
                registry_path=str(registry_path),
            )
            == "widget"
        )


def test_memory_root_derives_default_paths(env, capsys):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    memory_root = env["base"] / "agents" / "memory"

    code = cli.main(["registry", "backfill-remotes", "--memory-root", str(memory_root)])
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report["registry"] == str(env["registry"])
    assert report["projects"] == str(env["legacy"])
    assert _status_by_slug(report) == {"widget": "add"}


def _race_on_lock(monkeypatch, write_other):
    """模擬「規劃之後、取得 registry lock 之前」另一個 writer 先寫入 registry。

    另一個 writer 必然在我們取得 LOCK_EX 之前完成寫入（它持有 lock 寫完才釋放），
    故在第一次 LOCK_EX 呼叫時注入其寫入，即為兩者之間的最壞交錯。
    """
    import fcntl

    from paulsha_hippo.importer import registry as registry_mod

    real_flock = registry_mod.fcntl.flock
    state = {"raced": False}

    def racing_flock(fd, operation):
        if operation == fcntl.LOCK_EX and not state["raced"]:
            state["raced"] = True
            write_other()
        return real_flock(fd, operation)

    monkeypatch.setattr(registry_mod.fcntl, "flock", racing_flock)
    return state


def test_apply_backs_up_registry_created_by_other_writer_before_lock(env, capsys, monkeypatch):
    # 審查 #161 第二輪-1：規劃時 registry 還不存在（僅 legacy 有 root），取得 lock 前另一個
    # writer 建立了 registry。備份判斷若在 lock 外，這次寫入會覆蓋真檔卻沒有備份，回復指令
    # 還會是 `rm`（連同對方內容一起刪掉）。
    repo = _repo(env["base"] / "legacy-proj", remote="git@github.com:acme/legacy-proj.git")
    env["legacy"].write_text(f"projects:\n  legacy-proj:\n    roots:\n      - {repo}\n", encoding="utf-8")
    assert not env["registry"].exists()
    other = render_registry((ProjectConfig(slug="other", roots=("/data/other",)),))
    state = _race_on_lock(monkeypatch, lambda: env["registry"].write_text(other, encoding="utf-8"))

    code, report = _run(env, "--apply", capsys=capsys)

    assert state["raced"] is True
    assert code == 0
    assert report["changed"] is True
    backups = _backups(env["registry"])
    assert [str(path) for path in backups] == [report["backup"]]
    assert backups[0].read_text(encoding="utf-8") == other
    assert report["restore"].startswith("cp -p ")
    slugs = {p.slug: p.remotes for p in parse_registry(env["registry"].read_text(encoding="utf-8"))}
    assert slugs == {"other": (), "legacy-proj": ("github.com/acme/legacy-proj",)}
    subprocess.run(report["restore"], shell=True, check=True)
    assert env["registry"].read_text(encoding="utf-8") == other


def test_apply_backup_is_the_version_actually_overwritten(env, capsys, monkeypatch):
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    updated = render_registry(
        (
            ProjectConfig(slug="widget", roots=(str(repo),)),
            ProjectConfig(slug="other", roots=("/data/other",)),
        )
    )
    _race_on_lock(monkeypatch, lambda: env["registry"].write_text(updated, encoding="utf-8"))

    _code, report = _run(env, "--apply", capsys=capsys)

    assert Path(report["backup"]).read_text(encoding="utf-8") == updated


def test_apply_when_other_writer_already_converged_is_success_without_change(env, capsys, monkeypatch):
    # 審查 #161 第二輪-2：另一個行程先補上同一個 remote，結果已收斂——應回報成功（exit 0）
    # 並說明未變更，不得誤報成 schema_version 拒寫。
    repo = _repo(env["base"] / "widget")
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )
    converged = render_registry(
        (ProjectConfig(slug="widget", roots=(str(repo),), remotes=("github.com/acme/widget",)),)
    )
    _race_on_lock(monkeypatch, lambda: env["registry"].write_text(converged, encoding="utf-8"))

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 0
    assert "error" not in report
    assert report["changed"] is False
    assert report["write_status"] == "unchanged"
    assert report["backup"] is None and report["restore"] is None
    assert _backups(env["registry"]) == []
    assert env["registry"].read_text(encoding="utf-8") == converged


def test_apply_refused_by_newer_schema_is_an_error(env, capsys):
    repo = _repo(env["base"] / "widget")
    newer = (
        "schema_version: 2\n"
        "projects:\n"
        f"  - slug: \"widget\"\n    roots:\n      - \"{repo}\"\n    remotes: []\n    aliases: []\n"
    )
    env["registry"].write_text(newer, encoding="utf-8")

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 1
    assert report["write_status"] == "refused-schema"
    assert "schema_version" in report["error"]
    assert report["changed"] is False and report["backup"] is None
    assert env["registry"].read_text(encoding="utf-8") == newer
    assert _backups(env["registry"]) == []


def test_apply_with_nothing_to_add_reports_status(env, capsys):
    repo = _repo(env["base"] / "widget", remote=None)
    env["registry"].write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo),)),)), encoding="utf-8"
    )

    code, report = _run(env, "--apply", capsys=capsys)

    assert code == 0
    assert report["write_status"] == "nothing-to-add"
    assert report["changed"] is False


def test_same_second_backups_never_overwrite_each_other(tmp_path):
    """同一秒（同一 backup_path）兩次成功寫入：第二次不得覆寫第一次的備份（審查 #161 第三輪）。"""
    from paulsha_hippo.importer.registry import record_discoveries

    registry = tmp_path / "project-hippo.yaml"
    registry.write_text(
        render_registry((ProjectConfig(slug="widget", roots=("/r/widget",)),)),
        encoding="utf-8",
    )
    v0 = registry.read_bytes()
    backup = tmp_path / "project-hippo.yaml.bak-117-20260101T000000Z"

    first = record_discoveries(
        [ProjectConfig(slug="widget", roots=("/r/widget",), remotes=("github.com/acme/widget",))],
        registry_path=registry,
        backup_path=backup,
    )
    v1 = registry.read_bytes()
    second = record_discoveries(
        [ProjectConfig(slug="gadget", roots=("/r/gadget",), remotes=("github.com/acme/gadget",))],
        registry_path=registry,
        backup_path=backup,
    )

    assert first.status == second.status == "written"
    assert first.backup is not None and second.backup is not None
    assert first.backup != second.backup
    assert first.backup.read_bytes() == v0
    assert second.backup.read_bytes() == v1
