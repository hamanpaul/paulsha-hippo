"""Project registry producer（#14）：generated project-hippo.yaml 的 render/parse/寫入。

契約文件：docs/project-registry-contract.md（schema_version 對應）。
stdlib-only；手寫 YAML 子集 parser 沿用 importer/config.py 既有模式。
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import Iterable, Sequence

from paulsha_hippo import paths

from .config import (
    ProjectConfig,
    ProjectsConfig,
    _trimmed_lines,
    load_projects_config,
)

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


def _quote_scalar(value: str) -> str:
    """動態字串值一律輸出 YAML double-quoted scalar（契約 §4）。

    只需 escape `\\` 與 `"`——double-quoted style 對 `#`（註解）、`: `（mapping）、
    `[` `]` `,`（flow）、前導／尾隨空白等全部安全；不加引號的 plain scalar 會被
    標準 YAML parser（獨立 consumer 如 cortex）誤讀（#14 blocking）。
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote_scalar(value: str) -> str:
    """讀回 scalar：double-quoted 解 escape（`\\\\`、`\\\"`）；single-quoted 解 `''`；
    其餘視為 legacy plain 形（quoting 改版前落盤的 v1 檔），去頭尾空白原樣回傳。"""
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        body = text[1:-1]
        chars: list[str] = []
        index = 0
        while index < len(body):
            char = body[index]
            if char == "\\" and index + 1 < len(body) and body[index + 1] in ('"', "\\"):
                chars.append(body[index + 1])
                index += 2
                continue
            chars.append(char)
            index += 1
        return "".join(chars)
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    return text


def _parse_inline_list(value: str) -> tuple[str, ...]:
    """Quote-aware inline list（`["a, b", "x"]`）：引號內的 `,` 不切分、escape 依 scalar 規則。

    config._inline_list 只做裸 split(",")，對 quoted 值會切錯——registry 檔自
    quoting 契約後必須用本函式；legacy 裸值（`[a1, a2]`）行為與舊版一致。
    """
    stripped = value.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return ()
    body = stripped[1:-1]
    pieces: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in body:
        if quote == '"':
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            continue
        if quote == "'":
            current.append(char)
            if char == "'":
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            current.append(char)
            continue
        if char == ",":
            pieces.append("".join(current))
            current = []
            continue
        current.append(char)
    pieces.append("".join(current))
    items = []
    for piece in pieces:
        chunk = piece.strip()
        if chunk:
            items.append(_unquote_scalar(chunk))
    return tuple(items)


def render_registry(projects: Iterable[ProjectConfig]) -> str:
    """輸出 canonical bytes：slug 字典序、各清單去重排序、LF、檔尾恰一換行；
    所有動態字串值套 _quote_scalar（double-quoted，契約 §4 quoting 規則）。"""
    lines: list[str] = list(GENERATED_HEADER_LINES)
    lines.append(f"schema_version: {SCHEMA_VERSION}")
    ordered = sorted(projects, key=lambda project: project.slug)
    if not ordered:
        lines.append("projects: []")
        return "\n".join(lines) + "\n"
    lines.append("projects:")
    for project in ordered:
        lines.append(f"  - slug: {_quote_scalar(project.slug)}")
        for key, values in (("roots", project.roots), ("remotes", project.remotes)):
            deduped = sorted(set(values))
            if deduped:
                lines.append(f"    {key}:")
                lines.extend(f"      - {_quote_scalar(item)}" for item in deduped)
            else:
                lines.append(f"    {key}: []")
        alias_values = sorted(set(project.aliases))
        if alias_values:
            rendered_aliases = ", ".join(_quote_scalar(alias) for alias in alias_values)
            lines.append(f"    aliases: [{rendered_aliases}]")
        else:
            lines.append("    aliases: []")
    return "\n".join(lines) + "\n"


def _finalize_registry_item(
    projects: list[ProjectConfig], current: dict[str, list[str] | str] | None
) -> None:
    if current is None:
        return
    # slug 已於 parse 階段 unquote（quoted 形精確界定值域，含前導／尾隨空白），
    # 此處只做空值檢查、不再 strip 值本身。
    slug = str(current.get("slug") or "")
    if not slug.strip():
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
                    current["slug"] = _unquote_scalar(value)
            continue
        if current is None:
            continue
        if indent == 4 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key in {"roots", "remotes"}:
                if value.startswith("["):
                    current[key] = list(_parse_inline_list(value))
                    current_list_key = None
                else:
                    current[key] = []
                    current_list_key = key
                continue
            if key == "aliases":
                current["aliases"] = list(_parse_inline_list(value))
                current_list_key = None
                continue
            current[key] = _unquote_scalar(value)
            current_list_key = None
            continue
        if indent >= 6 and stripped.startswith("- ") and current_list_key in {"roots", "remotes"}:
            current.setdefault(current_list_key, []).append(_unquote_scalar(stripped[2:]))
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
    except (OSError, ValueError):
        # UnicodeDecodeError ⊂ ValueError：壞 bytes 同樣 fail-open 回空，不阻斷讀取端。
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


def merge_discovery(
    existing: Iterable[ProjectConfig], incoming: ProjectConfig
) -> tuple[ProjectConfig, ...]:
    """同 slug 併集（roots/remotes/aliases 去重排序）；新 slug 追加。"""
    merged: list[ProjectConfig] = []
    found = False
    for project in existing:
        if project.slug != incoming.slug:
            merged.append(project)
            continue
        found = True
        merged.append(
            ProjectConfig(
                slug=project.slug,
                roots=tuple(sorted(set(project.roots) | set(incoming.roots))),
                remotes=tuple(sorted(set(project.remotes) | set(incoming.remotes))),
                aliases=tuple(sorted(set(project.aliases) | set(incoming.aliases))),
            )
        )
    if not found:
        merged.append(incoming)
    return tuple(merged)


def record_discovery(
    *,
    slug: str,
    roots: Sequence[str] = (),
    remotes: Sequence[str] = (),
    aliases: Sequence[str] = (),
    registry_path: str | Path,
) -> bool:
    """把一筆 discovery 併入 generated registry；回傳檔案是否變更。

    互斥：同目錄固定名 lock（LOCK_FILENAME）flock(LOCK_EX)——固定名、非 per-key，
    不產生無界 lock namespace（對照 #19 教訓）。內容未變則跳寫（冪等）。
    前向防護：既有檔 schema_version 高於 SCHEMA_VERSION 時拒寫並記 warning
    （回 False），避免舊 producer 把新版檔案降級重繪、刪除未知欄位。
    """
    if not slug:
        raise ValueError("slug must be non-empty")
    path = Path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(LOCK_FILENAME)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            try:
                existing_text: str | None = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                # 壞 bytes 視同缺檔：下一筆 discovery 重寫 canonical bytes（自癒，
                # 契約 §5 手改情境以 canonical 化覆蓋而非 crash）。
                existing_text = None
            if existing_text is not None:
                existing_version = registry_schema_version(existing_text)
                if existing_version is not None and existing_version > SCHEMA_VERSION:
                    # 前向防護（契約 §7）：新版 producer 已寫入更高 schema_version 時，
                    # 本 v1 writer 若照舊 parse→render 會把檔案降級重繪、刪除未知欄位
                    # （混版部署下永久資料遺失）。無顯式 migration 前一律拒寫。
                    LOGGER.warning(
                        "project registry schema_version %s 高於本 producer 支援的 %s，"
                        "拒絕以 v%s 重寫（避免降級刪除新版欄位），跳過本筆 discovery: %s",
                        existing_version,
                        SCHEMA_VERSION,
                        SCHEMA_VERSION,
                        path,
                    )
                    return False
            existing = parse_registry(existing_text) if existing_text is not None else ()
            incoming = ProjectConfig(
                slug=slug,
                roots=tuple(str(item) for item in roots if item),
                remotes=tuple(str(item) for item in remotes if item),
                aliases=tuple(str(item) for item in aliases if item),
            )
            rendered = render_registry(merge_discovery(existing, incoming))
            if existing_text == rendered:
                return False
            tmp_path = path.with_name(TMP_FILENAME)
            tmp_path.write_text(rendered, encoding="utf-8")
            os.replace(tmp_path, path)
            return True
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def auto_write_enabled(config_path: str | Path | None = None) -> bool:
    """讀 project_registry.auto_write（預設 off）；缺檔／壞檔／缺鍵一律 False（opt-in）。"""
    path = Path(config_path) if config_path is not None else paths.hippo_config_root() / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # corrupt config.yaml（UnicodeDecodeError）不得炸穿 ingest：本函式在
        # pipeline._record_registry_discovery 的 try 之外被呼叫，fail-open 回 False。
        return False
    in_section = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            in_section = stripped == "project_registry:"
            continue
        if in_section and ":" in stripped:
            key, value = stripped.split(":", 1)
            if key.strip() == "auto_write":
                return value.strip().strip("\"'").lower() in {"true", "yes", "on", "1"}
    return False


def load_union_projects_config(
    legacy_path: str | Path | None,
    registry_path: str | Path | None,
) -> ProjectsConfig:
    """Union-read：legacy projects.yaml（manual）∪ project-hippo.yaml（generated）。

    Manual 條目在前；同 slug 併 roots/remotes/aliases（manual 值序在前）；
    alias 衝突 manual 優先並記 warning。legacy 檔不搬移不改寫（非破壞過渡）。
    """
    legacy = load_projects_config(legacy_path)
    discovered = load_registry(registry_path)
    if not discovered:
        return legacy
    merged: list[ProjectConfig] = []
    index_by_slug: dict[str, int] = {}
    for project in legacy.projects:
        index_by_slug[project.slug] = len(merged)
        merged.append(project)
    for project in discovered:
        index = index_by_slug.get(project.slug)
        if index is None:
            index_by_slug[project.slug] = len(merged)
            merged.append(project)
            continue
        base = merged[index]
        merged[index] = ProjectConfig(
            slug=base.slug,
            roots=base.roots + tuple(item for item in project.roots if item not in base.roots),
            remotes=base.remotes
            + tuple(item for item in project.remotes if item not in base.remotes),
            aliases=base.aliases
            + tuple(item for item in project.aliases if item not in base.aliases),
        )
    aliases: dict[str, str] = {}
    for project in merged:
        for alias in project.aliases:
            if alias in aliases:
                if aliases[alias] != project.slug:
                    LOGGER.warning(
                        "alias collision for %s: keeping %s, ignoring %s",
                        alias,
                        aliases[alias],
                        project.slug,
                    )
                continue
            aliases[alias] = project.slug
    return ProjectsConfig(projects=tuple(merged), aliases=aliases)
