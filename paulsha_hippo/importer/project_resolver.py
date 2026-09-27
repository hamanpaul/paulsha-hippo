"""Project resolution helpers for Stage 2 memory importer."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from . import _git
from .config import ProjectsConfig, default_projects_path
from .registry import default_registry_path, load_union_projects_config


def _path_parts(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return PurePosixPath(str(value).replace("\\", "/")).parts


def _best_root_match(candidate: str | None, projects: ProjectsConfig) -> str | None:
    candidate_parts = _path_parts(candidate)
    if not candidate_parts:
        return None
    best_slug: str | None = None
    best_length = -1
    for project in projects.projects:
        for root in project.roots:
            root_parts = _path_parts(root)
            if not root_parts:
                continue
            if len(root_parts) > len(candidate_parts):
                continue
            if candidate_parts[: len(root_parts)] != root_parts:
                continue
            if len(root_parts) > best_length:
                best_slug = project.slug
                best_length = len(root_parts)
    return best_slug


def normalize_remote(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().replace("\\", "/")
    normalized = normalized.rstrip("/")
    if "://" in normalized:
        parsed = urlsplit(normalized)
        try:
            port = parsed.port
        except ValueError:
            return ""
        host = parsed.hostname or ""
        if port and not (
            parsed.scheme.lower() == "ssh" and host.lower() == "github.com" and port == 22
        ):
            host = f"{host}:{port}"
        path = parsed.path.lstrip("/") if host else parsed.path
        normalized = "/".join(part for part in (host, path) if part)
    else:
        normalized = re.sub(r"^[^/@:]+@", "", normalized)
        if ":" in normalized and "/" not in normalized.split(":", 1)[0]:
            normalized = normalized.replace(":", "/", 1)
    normalized = normalized.rstrip("/")
    normalized = re.sub(r"\.git$", "", normalized, flags=re.IGNORECASE)
    if normalized.count("/") == 1 and "." not in normalized.split("/", 1)[0]:
        normalized = f"github.com/{normalized}"
    parts = [part for part in normalized.strip("/").split("/") if part]
    if not parts:
        return ""
    parts[0] = parts[0].lower()
    if parts[0] == "github.com":
        parts[1:] = [part.lower() for part in parts[1:]]
    return "/".join(parts)


LOGGER = logging.getLogger("paulsha_hippo.importer")

UNKNOWN_PROJECT = "_unknown"

EPHEMERAL_ROOTS_ENV = "HIPPO_EPHEMERAL_ROOTS"
_DEFAULT_EPHEMERAL_ROOTS = ("/tmp", "/var/tmp", "/dev/shm")


def _is_same_or_ancestor(candidate: str, path: str) -> bool:
    return path == candidate or path.startswith(candidate.rstrip("/") + "/")


def ephemeral_roots() -> tuple[str, ...]:
    """暫存根目錄（#117）：其下無 remote、未登記的 checkout 不具穩定身分。

    `HIPPO_EPHEMERAL_ROOTS`（以 os.pathsep 分隔的絕對路徑）有設定時完全取代預設值，
    空字串即停用；未設定時取系統暫存目錄（`/tmp`、`/var/tmp`、`/dev/shm` 與
    `tempfile.gettempdir()`）。檔案系統根 `/`、HOME 本身與其祖先一律排除——暫存根
    誤設成這些位置會讓整台機器的 session 全歸 `_unknown`。
    """
    raw = os.environ.get(EPHEMERAL_ROOTS_ENV)
    if raw is not None:
        candidates = [item.strip() for item in raw.split(os.pathsep)]
    else:
        candidates = list(_DEFAULT_EPHEMERAL_ROOTS)
        try:
            candidates.append(tempfile.gettempdir())
        except Exception:
            pass
    try:
        home = os.path.realpath(str(Path.home()))
    except Exception:
        home = ""
    roots: list[str] = []
    for candidate in candidates:
        if not candidate or not os.path.isabs(candidate):
            continue
        real = os.path.realpath(candidate)
        if real == "/" or (home and _is_same_or_ancestor(real, home)):
            continue
        if real not in roots:
            roots.append(real)
    return tuple(roots)


def is_ephemeral_path(path: str | os.PathLike[str] | None) -> bool:
    """path 是否為暫存根本身或其子孫（字面與 realpath 兩種形式皆比對）。"""
    if not path:
        return False
    text = str(path)
    if not os.path.isabs(text):
        return False
    forms = {os.path.normpath(text), os.path.realpath(text)}
    return any(
        _is_same_or_ancestor(root, form) for root in ephemeral_roots() for form in forms
    )


def _ephemeral_unknown(path: str) -> str:
    LOGGER.debug(
        "project unresolved（ephemeral root 下無 remote、未登記的 checkout 歸 %s）: %s；"
        "長期工作目錄請於 projects.yaml 登記 roots，或以 %s 調整暫存根",
        UNKNOWN_PROJECT,
        path,
        EPHEMERAL_ROOTS_ENV,
    )
    return UNKNOWN_PROJECT


def _main_toplevel(toplevel: str) -> str:
    """linked worktree → 主 repo root；其他情形回傳輸入（best-effort、never raises）。"""
    try:
        return _git.git_main_toplevel(toplevel) or toplevel
    except Exception:
        return toplevel


def resolve_project(
    *,
    cwd: str | None = None,
    git_toplevel: str | None = None,
    remote_url: str | None = None,
    projects: ProjectsConfig | None = None,
    config_path: str | None = None,
    memory_root: str | None = None,
    registry_path: str | None = None,
) -> str:
    loaded_projects = projects
    if loaded_projects is None:
        # union-read（#14 過渡）：legacy projects.yaml ∪ generated project-hippo.yaml
        loaded_projects = load_union_projects_config(
            config_path or default_projects_path(memory_root),
            registry_path or default_registry_path(memory_root),
        )
    for candidate in (cwd, git_toplevel):
        matched = _best_root_match(candidate, loaded_projects)
        if matched:
            return matched
    normalized_remote = normalize_remote(remote_url)
    if normalized_remote:
        for project in loaded_projects.projects:
            for remote in project.remotes:
                if normalize_remote(remote) == normalized_remote:
                    return project.slug

    try:
        toplevel = _git.git_toplevel(cwd)
    except Exception:
        toplevel = None

    if toplevel:
        # worktree 收斂（#117）：`<repo>-worktrees/<branch>` 這類不在主 repo root 前綴下的
        # linked worktree，歸併為主 repo root 後再比對 roots——registry 只登記 roots、
        # 尚無 remotes 時也能收斂到既有 slug，不落到 raw remote 另開 bucket。
        identity_root = _main_toplevel(toplevel)
        if identity_root != toplevel:
            matched = _best_root_match(identity_root, loaded_projects)
            if matched:
                return matched
        try:
            remote = normalize_remote(_git.git_remote(toplevel))
        except Exception:
            remote = ""
        if remote:
            # payloads rarely carry remote_url, so this fallback is where most
            # repos resolve. Map the discovered remote through projects.yaml to a
            # slug before falling back to the raw URL form (else registered
            # remotes never take effect for nested/unlisted working dirs).
            for project in loaded_projects.projects:
                if any(normalize_remote(value) == remote for value in project.remotes):
                    return project.slug
            return remote

        # 無 remote、未登記：暫存目錄下的 checkout 不具穩定身分，歸既有 unresolved 類別
        # `_unknown`（下游 wakeup／shortlist／registry discovery 皆已視為不注入、不落盤），
        # 不以目錄名產生假 project（#117：`checkout` bucket）。
        if is_ephemeral_path(identity_root):
            return _ephemeral_unknown(identity_root)
        name = Path(identity_root).name
        if name:
            try:
                if _git.sibling_repo_count(identity_root) >= 2:
                    parent = Path(identity_root).parent.name
                    return f"{parent}/{name}" if parent else name
            except Exception:
                pass
            return name

    if cwd:
        if is_ephemeral_path(cwd):
            return _ephemeral_unknown(cwd)
        return Path(cwd).name or UNKNOWN_PROJECT
    return UNKNOWN_PROJECT
