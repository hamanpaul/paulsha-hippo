"""Project registry remotes 補登（#117）。

registry 只登記 roots、沒有 remotes 的 project，在 `<repo>-worktrees/<branch>` 這類
不在 root 前綴下的 cwd 會落到 raw remote 另開 knowledge bucket。本模組提供兩條補登路徑
共用的判準：

- 註冊時（importer discovery）：slug 由「恰等於主 repo root 的 registered root」派生時，
  以現場 git 探測到的 origin remote 補進該 slug（`root_registered_remote`）。
- 既有 registry 一次性 backfill：逐一探測各 project 的 roots（`plan_remote_backfill`），
  預設只產生計畫；`apply_remote_backfill` 才寫入，寫前備份並回傳回復指令。

安全邊界（兩條路徑一致）：
- 只信現場 `git remote get-url origin`，payload 夾帶的 remote 不得經此路徑落盤。
- registered root 必須**恰為** repo toplevel；只是祖先目錄（workspace）時不補登，
  否則巢狀 repo 的 remote 會把同 remote 的所有 checkout 吸進 workspace slug。
- 同一 remote 已由其他 slug 認領（或本次計畫中有多個 slug 探測到同一 remote）→ conflict，
  不補登——一個 remote 對多個 slug 會讓解析取決於清單順序，task-memory provider 也會判
  scope-mismatch。
"""

from __future__ import annotations

import os
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import _git
from .config import ProjectConfig, ProjectsConfig
from .project_resolver import normalize_remote
from .registry import record_discoveries

STATUS_ADD = "add"
STATUS_PRESENT = "present"
STATUS_CONFLICT = "conflict"
STATUS_ROOT_MISSING = "root-missing"
STATUS_NOT_REPO = "not-a-repo"
STATUS_NOT_REPO_ROOT = "not-repo-root"
STATUS_NO_REMOTE = "no-remote"

BACKUP_SUFFIX = ".bak-117-"


def _path_forms(value: str | os.PathLike[str]) -> set[str]:
    text = str(value)
    return {os.path.normpath(text), os.path.realpath(text)}


def same_path(left: str | os.PathLike[str] | None, right: str | os.PathLike[str] | None) -> bool:
    """兩路徑是否指向同一位置（字面 normpath 或 realpath 任一相等）。"""
    if not left or not right:
        return False
    return bool(_path_forms(left) & _path_forms(right))


def registered_root_owners(root: str | None, projects: ProjectsConfig) -> set[str]:
    """roots 中**恰好**登記了 root（非前綴）的 slug 集合。"""
    if not root:
        return set()
    return {
        project.slug
        for project in projects.projects
        if any(same_path(registered, root) for registered in project.roots)
    }


def remote_claimants(remote: str, projects: ProjectsConfig) -> set[str]:
    """remotes 經正規化後等於 remote 的 slug 集合。"""
    normalized = normalize_remote(remote)
    if not normalized:
        return set()
    return {
        project.slug
        for project in projects.projects
        if any(normalize_remote(value) == normalized for value in project.remotes)
    }


def root_registered_remote(
    *, slug: str, main_root: str | None, probed_remote: str, projects: ProjectsConfig
) -> str:
    """註冊時補登判準：回傳可補進 slug 的 remote，不符合則回空字串。"""
    remote = normalize_remote(probed_remote)
    if not slug or not main_root or not remote:
        return ""
    if registered_root_owners(main_root, projects) != {slug}:
        return ""
    if remote_claimants(remote, projects) - {slug}:
        return ""
    return remote


def probe_root(root: str) -> dict[str, str]:
    """探測單一 registered root：回傳 {"status", "remote"}（best-effort、never raises）。"""
    if not root or not os.path.isabs(root) or not Path(root).is_dir():
        return {"status": STATUS_ROOT_MISSING, "remote": ""}
    try:
        toplevel = _git.git_toplevel(root)
    except Exception:
        toplevel = None
    if not toplevel:
        return {"status": STATUS_NOT_REPO, "remote": ""}
    if not same_path(toplevel, root):
        return {"status": STATUS_NOT_REPO_ROOT, "remote": ""}
    try:
        remote = normalize_remote(_git.git_remote(toplevel))
    except Exception:
        remote = ""
    if not remote:
        return {"status": STATUS_NO_REMOTE, "remote": ""}
    return {"status": "probed", "remote": remote}


def plan_remote_backfill(
    projects: ProjectsConfig,
    *,
    probe: Callable[[str], dict[str, str]] = probe_root,
) -> list[dict[str, Any]]:
    """逐一探測 union config 各 project 的 roots，產生補登計畫（不寫任何檔案）。"""
    probed: list[dict[str, Any]] = []
    for project in projects.projects:
        for root in project.roots:
            result = probe(root)
            probed.append(
                {
                    "slug": project.slug,
                    "root": root,
                    "status": result.get("status", STATUS_NOT_REPO),
                    "remote": result.get("remote", ""),
                    "claimed_by": [],
                }
            )
    planned_owners: dict[str, set[str]] = {}
    for item in probed:
        if item["status"] == "probed":
            planned_owners.setdefault(item["remote"], set()).add(item["slug"])
    slugs_by_name = {project.slug: project for project in projects.projects}
    for item in probed:
        if item["status"] != "probed":
            continue
        remote = item["remote"]
        others = (remote_claimants(remote, projects) | planned_owners.get(remote, set())) - {item["slug"]}
        if others:
            item["status"] = STATUS_CONFLICT
            item["claimed_by"] = sorted(others)
            continue
        existing = {normalize_remote(value) for value in slugs_by_name[item["slug"]].remotes}
        item["status"] = STATUS_PRESENT if remote in existing else STATUS_ADD
    return probed


def summarize_plan(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(item["status"] for item in items).items()))


def planned_additions(items: list[dict[str, Any]]) -> tuple[ProjectConfig, ...]:
    """計畫中 status=add 的項目，依 slug 合併為待寫入的 discovery 條目。"""
    grouped: dict[str, tuple[set[str], set[str]]] = {}
    for item in items:
        if item["status"] != STATUS_ADD:
            continue
        roots, remotes = grouped.setdefault(item["slug"], (set(), set()))
        roots.add(item["root"])
        remotes.add(item["remote"])
    return tuple(
        ProjectConfig(slug=slug, roots=tuple(sorted(roots)), remotes=tuple(sorted(remotes)))
        for slug, (roots, remotes) in sorted(grouped.items())
    )


def overlay_additions(projects: ProjectsConfig, additions: tuple[ProjectConfig, ...]) -> ProjectsConfig:
    """把計畫中的補登 remotes 疊到 union config（in-memory，供 impact report 模擬補登後的解析）。"""
    extra = {entry.slug: entry.remotes for entry in additions}
    merged = tuple(
        ProjectConfig(
            slug=project.slug,
            roots=project.roots,
            remotes=project.remotes
            + tuple(remote for remote in extra.get(project.slug, ()) if remote not in project.remotes),
            aliases=project.aliases,
        )
        for project in projects.projects
    )
    return ProjectsConfig(projects=merged, aliases=dict(projects.aliases or {}), families=projects.families)


def backup_path_for(registry_path: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return registry_path.with_name(f"{registry_path.name}{BACKUP_SUFFIX}{stamp}")


def apply_remote_backfill(
    items: list[dict[str, Any]],
    registry_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """寫入計畫中的補登（冪等）；有變更且原檔存在時先備份，回傳回復指令。"""
    path = Path(registry_path)
    additions = planned_additions(items)
    if not additions:
        return {"changed": False, "backup": None, "restore": None}
    existed = path.exists()
    backup = backup_path_for(path, now) if existed else None
    changed = record_discoveries(additions, registry_path=path, backup_path=backup)
    if not changed:
        return {"changed": False, "backup": None, "restore": None}
    if backup is not None and backup.exists():
        restore = f"cp -p {shlex.quote(str(backup))} {shlex.quote(str(path))}"
        backup_value: str | None = str(backup)
    else:
        restore = f"rm {shlex.quote(str(path))}"
        backup_value = None
    return {"changed": True, "backup": backup_value, "restore": restore}
