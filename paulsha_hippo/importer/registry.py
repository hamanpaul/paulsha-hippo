"""Project registry producer（#14）：generated project-hippo.yaml 的 render/parse/寫入。

契約文件：docs/project-registry-contract.md（schema_version 對應）。
stdlib-only；手寫 YAML 子集 parser 沿用 importer/config.py 既有模式。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from paulsha_hippo import paths

from .config import ProjectConfig, _inline_list, _trimmed_lines

LOGGER = logging.getLogger("paulsha_hippo.importer")

SCHEMA_VERSION = 1
REGISTRY_FILENAME = "project-hippo.yaml"
LOCK_FILENAME = ".project-hippo.yaml.lock"
TMP_FILENAME = ".project-hippo.yaml.tmp"

GENERATED_HEADER_LINES = (
    "# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。",
    "# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。",
    "# contract: docs/project-registry-contract.md",
)


def default_registry_path(memory_root: str | Path | None = None) -> Path:
    return paths.project_registry_path(memory_root)


def render_registry(projects: Iterable[ProjectConfig]) -> str:
    """輸出 canonical bytes：slug 字典序、各清單去重排序、LF、檔尾恰一換行。"""
    lines: list[str] = list(GENERATED_HEADER_LINES)
    lines.append(f"schema_version: {SCHEMA_VERSION}")
    ordered = sorted(projects, key=lambda project: project.slug)
    if not ordered:
        lines.append("projects: []")
        return "\n".join(lines) + "\n"
    lines.append("projects:")
    for project in ordered:
        lines.append(f"  - slug: {project.slug}")
        for key, values in (("roots", project.roots), ("remotes", project.remotes)):
            deduped = sorted(set(values))
            if deduped:
                lines.append(f"    {key}:")
                lines.extend(f"      - {item}" for item in deduped)
            else:
                lines.append(f"    {key}: []")
        alias_values = sorted(set(project.aliases))
        if alias_values:
            lines.append(f"    aliases: [{', '.join(alias_values)}]")
        else:
            lines.append("    aliases: []")
    return "\n".join(lines) + "\n"


def _finalize_registry_item(
    projects: list[ProjectConfig], current: dict[str, list[str] | str] | None
) -> None:
    if current is None:
        return
    slug = str(current.get("slug") or "").strip()
    if not slug:
        return
    projects.append(
        ProjectConfig(
            slug=slug,
            roots=tuple(str(item) for item in current.get("roots", [])),
            remotes=tuple(str(item) for item in current.get("remotes", [])),
            aliases=tuple(str(item) for item in current.get("aliases", [])),
        )
    )


def parse_registry(text: str) -> tuple[ProjectConfig, ...]:
    projects: list[ProjectConfig] = []
    current: dict[str, list[str] | str] | None = None
    current_list_key: str | None = None
    in_projects = False
    for indent, line in _trimmed_lines(text):
        stripped = line.strip()
        if indent == 0:
            _finalize_registry_item(projects, current)
            current = None
            current_list_key = None
            in_projects = stripped == "projects:"
            continue
        if not in_projects:
            continue
        if indent == 2 and stripped.startswith("- "):
            _finalize_registry_item(projects, current)
            current = {}
            current_list_key = None
            rest = stripped[2:].strip()
            if ":" in rest:
                key, value = rest.split(":", 1)
                if key.strip() == "slug":
                    current["slug"] = value.strip().strip("\"'")
            continue
        if current is None:
            continue
        if indent == 4 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key in {"roots", "remotes"}:
                if value.startswith("["):
                    current[key] = list(_inline_list(value))
                    current_list_key = None
                else:
                    current[key] = []
                    current_list_key = key
                continue
            if key == "aliases":
                current["aliases"] = list(_inline_list(value))
                current_list_key = None
                continue
            current[key] = value.strip("\"'")
            current_list_key = None
            continue
        if indent >= 6 and stripped.startswith("- ") and current_list_key in {"roots", "remotes"}:
            current.setdefault(current_list_key, []).append(stripped[2:].strip().strip("\"'"))
    _finalize_registry_item(projects, current)
    return tuple(projects)


def registry_schema_version(text: str) -> int | None:
    for indent, line in _trimmed_lines(text):
        stripped = line.strip()
        if indent == 0 and stripped.startswith("schema_version:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            try:
                return int(value)
            except ValueError:
                return None
    return None


def load_registry(path: str | Path | None) -> tuple[ProjectConfig, ...]:
    """讀 generated registry；缺檔／讀失敗回空（fail-open：registry 永不阻斷讀取端）。"""
    if path is None:
        return ()
    registry_path = Path(path)
    try:
        text = registry_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    version = registry_schema_version(text)
    if version is not None and version > SCHEMA_VERSION:
        LOGGER.warning(
            "project registry schema_version %s 高於支援的 %s，仍以 v%s 規則盡力解析: %s",
            version,
            SCHEMA_VERSION,
            SCHEMA_VERSION,
            registry_path,
        )
    return parse_registry(text)
