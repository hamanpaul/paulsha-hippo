from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from numbers import Integral
from pathlib import Path
from typing import Any

from paulsha_hippo.atomizer.config import AtomizerConfig, load_config
from paulsha_hippo.atomizer.splitter import Fragment, split
from paulsha_hippo.moc.frontmatter_io import read as read_frontmatter

LOGGER = logging.getLogger(__name__)


def _markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in sorted(root.rglob("*.md")) if not path.is_symlink()]


def _normalize_provenance(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): "" if raw is None else str(raw) for key, raw in value.items()}


def _session_identity(frontmatter: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(frontmatter.get("project") or "_unknown"),
        str(frontmatter.get("source_agent") or "_unknown"),
        str(frontmatter.get("source_session") or "_unknown"),
        str(frontmatter.get("source_artifact") or "_unknown"),
    )


def _missing_session_identity_fields(frontmatter: dict[str, Any]) -> list[str]:
    return [
        field
        for field in ("project", "source_agent", "source_session", "source_artifact")
        if not frontmatter.get(field)
    ]


def _session_label(identity: tuple[str, str, str, str]) -> str:
    return ":".join(identity)


def _item_id(frontmatter: dict[str, Any], fragment_index: int) -> str:
    return f"{':'.join(_session_identity(frontmatter))}#{fragment_index}"


def _hash_fraction(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest, byteorder="big") / float(1 << (8 * len(digest)))


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def _strip_leading_frontmatter(text: str) -> str:
    _, body = _split_leading_frontmatter(text)
    return body


def _split_leading_frontmatter(text: str) -> tuple[str | None, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    raise ValueError("unclosed leading frontmatter")


def _reference_group(relative: Path) -> str:
    parents = relative.parts[:-1]
    if not parents:
        return relative.stem
    for index, part in enumerate(parents):
        if (part.endswith("Vault") or part == "root-note") and index + 1 < len(parents):
            return parents[index + 1]
    return parents[0]


def _is_persisted_fragment(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0] == "_slices")


def _make_fragment(frontmatter: dict[str, Any], body: str, fragment_index: int) -> Fragment:
    project, source_agent, source_session, source_artifact = _session_identity(frontmatter)
    return Fragment(
        project=project,
        source_agent=source_agent,
        source_session=source_session,
        source_artifact=source_artifact,
        captured_at=str(frontmatter.get("captured_at") or "_unknown"),
        provenance=_normalize_provenance(frontmatter.get("provenance")),
        fragment_index=fragment_index,
        body=body.rstrip("\n"),
    )


def _coerce_fragment_index(frontmatter: dict[str, Any]) -> int:
    if "fragment_index" not in frontmatter:
        raise ValueError("missing fragment_index")

    raw = frontmatter.get("fragment_index")
    if isinstance(raw, bool):
        raise ValueError(f"invalid fragment_index {raw!r}")
    if isinstance(raw, Integral):
        fragment_index = int(raw)
    elif isinstance(raw, str):
        try:
            fragment_index = int(raw)
        except ValueError as exc:
            raise ValueError(f"invalid fragment_index {raw!r}") from exc
    else:
        raise ValueError(f"invalid fragment_index {raw!r}")
    if fragment_index < 0:
        raise ValueError(f"invalid fragment_index {raw!r}")
    return fragment_index


def load_inbox_items(
    inbox_root: str | Path,
    *,
    config: AtomizerConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config
    if cfg is None:
        cfg, _ = load_config(override_path=None)

    root = Path(inbox_root)
    items: list[dict[str, Any]] = []
    raw_docs: list[tuple[dict[str, Any], str]] = []
    persisted_fragments: dict[tuple[str, str, str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    invalid_persisted_sessions: set[tuple[str, str, str, str]] = set()

    for path in _markdown_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            LOGGER.warning("Skipping inbox doc %s due to unreadable content: %s", path, exc)
            continue
        try:
            _split_leading_frontmatter(text)
            frontmatter, body = read_frontmatter(text)
        except Exception as exc:
            LOGGER.warning("Skipping inbox doc %s due to invalid frontmatter: %s", path, exc)
            continue

        if not frontmatter.get("project") or not frontmatter.get("source_session"):
            continue

        session_identity = _session_identity(frontmatter)
        if _is_persisted_fragment(root, path):
            missing_identity_fields = _missing_session_identity_fields(frontmatter)
            if missing_identity_fields:
                LOGGER.warning(
                    "Skipping inbox doc %s due to incomplete session identity: missing %s",
                    path,
                    ", ".join(missing_identity_fields),
                )
                continue
            try:
                fragment_index = _coerce_fragment_index(frontmatter)
            except ValueError as exc:
                LOGGER.warning("Skipping inbox doc %s due to invalid fragment metadata: %s", path, exc)
                invalid_persisted_sessions.add(session_identity)
                continue
            if fragment_index in persisted_fragments[session_identity]:
                LOGGER.warning(
                    "Skipping inbox doc %s due to duplicate persisted fragment_index %d for session %s",
                    path,
                    fragment_index,
                    _session_label(session_identity),
                )
                invalid_persisted_sessions.add(session_identity)
                continue
            fragment = _make_fragment(frontmatter, body, fragment_index)
            persisted_fragments[session_identity][fragment_index] = {
                "id": _item_id(frontmatter, fragment_index),
                "project": fragment.project,
                "input": [fragment],
                "gold": {"project": fragment.project},
            }
            continue

        raw_docs.append((frontmatter, body))

    raw_sessions: set[tuple[str, str, str, str]] = set()
    for frontmatter, body in raw_docs:
        session_identity = _session_identity(frontmatter)
        raw_sessions.add(session_identity)
        raw_fragment_bodies = list(split(body, cfg))
        expected_indices = list(range(len(raw_fragment_bodies)))
        session_persisted = persisted_fragments.get(session_identity, {})

        if session_persisted:
            persisted_indices = sorted(session_persisted)
            if session_identity in invalid_persisted_sessions:
                LOGGER.warning(
                    "Ignoring persisted fragments for session %s due to invalid fragment metadata; falling back to raw session",
                    _session_label(session_identity),
                )
            elif persisted_indices == expected_indices:
                items.extend(session_persisted[index] for index in persisted_indices)
                continue
            else:
                LOGGER.warning(
                    "Ignoring persisted fragments for session %s due to incomplete persisted fragment coverage; expected indices %s but found %s. Falling back to raw session",
                    _session_label(session_identity),
                    expected_indices,
                    persisted_indices,
                )

        for fragment_index, fragment_body in enumerate(raw_fragment_bodies):
            fragment = _make_fragment(frontmatter, fragment_body, fragment_index)
            items.append(
                {
                    "id": _item_id(frontmatter, fragment_index),
                    "project": fragment.project,
                    "input": [fragment],
                    "gold": {"project": fragment.project},
                }
            )

    for session_identity, session_items in persisted_fragments.items():
        if session_identity in raw_sessions or session_identity in invalid_persisted_sessions:
            continue
        items.extend(session_items[index] for index in sorted(session_items))
    return items


def load_reference_slices(reference_root: str | Path) -> dict[str, list[dict[str, Any]]]:
    root = Path(reference_root)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in _markdown_files(root):
        relative = path.relative_to(root)
        if "PersonalVault" in relative.parts:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            LOGGER.warning("Skipping reference %s due to unreadable content: %s", path, exc)
            continue
        try:
            _, stripped_body = _split_leading_frontmatter(text)
        except ValueError as exc:
            LOGGER.warning("Skipping reference %s due to invalid frontmatter: %s", path, exc)
            continue
        try:
            body = _strip_leading_frontmatter(text)
        except Exception:
            body = stripped_body
        semantic_body = body.strip()
        if not semantic_body:
            continue

        domain = _reference_group(relative)
        grouped[domain].append(
            {
                "title": _extract_title(semantic_body, path.stem),
                "body": semantic_body,
                "tags": [],
            }
        )

    return {domain: grouped[domain] for domain in sorted(grouped)}


def build_valset(
    *,
    inbox_root: str | Path,
    reference_root: str | Path,
    config: AtomizerConfig | None = None,
    val_ratio: float = 0.2,
    min_project_sample: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    if not 0.0 <= float(val_ratio) <= 1.0:
        raise ValueError(f"val_ratio must be between 0 and 1 inclusive, got {val_ratio!r}")
    if int(min_project_sample) <= 0:
        raise ValueError(f"min_project_sample must be positive, got {min_project_sample!r}")

    items = load_inbox_items(inbox_root, config=config)
    if not items:
        return {"train": [], "val": []}

    references = load_reference_slices(reference_root)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item["project"])].append(item)

    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    threshold = float(val_ratio)

    for project in sorted(grouped):
        project_items = sorted(grouped[project], key=lambda item: str(item["id"]))
        reference_slices = list(references.get(project, []))
        sparse = len(project_items) < int(min_project_sample)
        if sparse:
            LOGGER.info(
                "Project %s has %d items; downgrading validation split to train because min_project_sample=%d",
                project,
                len(project_items),
                int(min_project_sample),
            )

        for item in project_items:
            target = train if sparse or _hash_fraction(str(item["id"])) >= threshold else val
            gold = {"project": project}
            if target is val:
                gold["reference_slices"] = list(reference_slices)
            target.append(
                {
                    "id": item["id"],
                    "input": item["input"],
                    "gold": gold,
                }
            )

    return {
        "train": sorted(train, key=lambda item: str(item["id"])),
        "val": sorted(val, key=lambda item: str(item["id"])),
    }
