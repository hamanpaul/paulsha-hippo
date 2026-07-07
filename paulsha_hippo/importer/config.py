"""Minimal config helpers for Stage 2 memory importer."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from paulsha_hippo import paths


LOGGER = logging.getLogger("paulsha_hippo.importer")


@dataclass(frozen=True)
class ProjectConfig:
    slug: str
    roots: tuple[str, ...] = ()
    remotes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectsConfig:
    projects: tuple[ProjectConfig, ...] = ()
    aliases: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.aliases is None:
            object.__setattr__(self, "aliases", {})


def default_projects_path(memory_root: str | Path | None = None) -> Path:
    return paths.projects_config_path(memory_root)


def _inline_list(value: str) -> tuple[str, ...]:
    stripped = value.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return ()
    body = stripped[1:-1].strip()
    if not body:
        return ()
    items = []
    for chunk in body.split(","):
        item = chunk.strip().strip("\"'")
        if item:
            items.append(item)
    return tuple(items)


def _trimmed_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), raw.rstrip()))
    return lines


def _finalize_project(projects: list[ProjectConfig], current_name: str | None, current_data: dict[str, list[str] | str]) -> None:
    if current_name is None:
        return
    slug = str(current_data.get("slug") or current_name)
    roots = tuple(str(item) for item in current_data.get("roots", []))
    remotes = tuple(str(item) for item in current_data.get("remotes", []))
    aliases = tuple(str(item) for item in current_data.get("aliases", []))
    projects.append(ProjectConfig(slug=slug, roots=roots, remotes=remotes, aliases=aliases))


def parse_projects_config(text: str) -> ProjectsConfig:
    lines = _trimmed_lines(text)
    projects: list[ProjectConfig] = []
    current_name: str | None = None
    current_data: dict[str, list[str] | str] = {}
    current_list_key: str | None = None
    in_projects = False

    for indent, line in lines:
        stripped = line.strip()
        if indent == 0 and stripped == "projects:":
            in_projects = True
            current_list_key = None
            continue
        if not in_projects:
            continue
        if indent == 2 and stripped.endswith(":"):
            _finalize_project(projects, current_name, current_data)
            current_name = stripped[:-1]
            current_data = {}
            current_list_key = None
            continue
        if current_name is None:
            continue
        if indent == 4 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key in {"roots", "remotes"}:
                current_data[key] = []
                current_list_key = key
                if value.startswith("- "):
                    current_data[key].append(value[2:].strip())
                continue
            if key == "aliases":
                current_data[key] = list(_inline_list(value))
                current_list_key = None
                continue
            current_data[key] = value.strip("\"'")
            current_list_key = None
            continue
        if indent >= 6 and stripped.startswith("- ") and current_list_key in {"roots", "remotes"}:
            current_data.setdefault(current_list_key, []).append(stripped[2:].strip())

    _finalize_project(projects, current_name, current_data)

    aliases: dict[str, str] = {}
    for project in projects:
        for alias in project.aliases:
            if alias in aliases:
                LOGGER.warning(
                    "alias collision for %s: keeping %s, ignoring %s",
                    alias,
                    aliases[alias],
                    project.slug,
                )
                continue
            aliases[alias] = project.slug
    return ProjectsConfig(projects=tuple(projects), aliases=aliases)


def load_projects_config(path: str | Path | None) -> ProjectsConfig:
    if path is None:
        return ProjectsConfig()
    config_path = Path(path)
    if not config_path.exists():
        return ProjectsConfig()
    return parse_projects_config(config_path.read_text(encoding="utf-8"))
