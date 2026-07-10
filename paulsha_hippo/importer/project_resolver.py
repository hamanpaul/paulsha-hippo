"""Project resolution helpers for Stage 2 memory importer."""

from __future__ import annotations

import re
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

        name = Path(toplevel).name
        if name:
            try:
                if _git.sibling_repo_count(toplevel) >= 2:
                    parent = Path(toplevel).parent.name
                    return f"{parent}/{name}" if parent else name
            except Exception:
                pass
            return name

    if cwd:
        return Path(cwd).name or "_unknown"
    return "_unknown"
