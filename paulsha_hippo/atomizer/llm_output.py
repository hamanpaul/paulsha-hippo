from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from paulsha_hippo.lib.lifecycle.schema import ARTIFACT_KINDS
from .config import is_safe_path_component

_LOG = logging.getLogger("paulsha_hippo.atomizer")

_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_JSON_DECODER = json.JSONDecoder()
_EMBEDDED_JSON_PREFIXES = frozenset({":", ",", "[", "{", '"'})
_EMBEDDED_JSON_SUFFIXES = frozenset({":", ",", "]", "}"})
_OUTPUT_ARRAY_LABEL_RE = re.compile(r"output:", re.IGNORECASE)
_PROPOSAL_ARRAY_WRAPPER_KEYS = frozenset({"findings", "slices", "proposals", "atoms"})
_PROPOSAL_KEYS = frozenset(
    {
        "title",
        "artifact_kind",
        "project",
        "tags",
        "body",
        "source_fragment_indices",
        "relations",
    }
)


class LlmOutputError(Exception):
    """Raised when agent output is missing, malformed, or schema-invalid."""


@dataclass(frozen=True)
class SliceProposal:
    title: str
    artifact_kind: str
    project: str
    tags: tuple[str, ...]
    body: str
    source_fragment_indices: tuple[int, ...]
    relations: tuple[dict[str, Any], ...]


def _iter_json_arrays(raw: str):
    for fenced in _FENCED_BLOCK_RE.finditer(raw):
        yield from _iter_json_array_candidates(fenced.group(1), offset=fenced.start(1))
        yield from _iter_wrapped_json_array_candidates(fenced.group(1), offset=fenced.start(1))

    yield from _iter_json_array_candidates(raw, offset=0)
    yield from _iter_wrapped_json_array_candidates(raw, offset=0)


def _iter_json_array_candidates(raw: str, offset: int):
    for start, character in enumerate(raw):
        if character != "[":
            continue
        try:
            candidate, end = _JSON_DECODER.raw_decode(raw, start)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, list) and _is_standalone_json_value(raw, start, end):
            yield candidate, offset + start, offset + end


def _iter_wrapped_json_array_candidates(raw: str, offset: int):
    for start, character in enumerate(raw):
        if character != "{":
            continue
        try:
            candidate, end = _JSON_DECODER.raw_decode(raw, start)
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict) or not _is_standalone_json_value(raw, start, end):
            continue
        if (unwrapped := _unwrap_wrapped_json_array_candidate(candidate)) is not None:
            yield unwrapped, offset + start, offset + end


def _unwrap_wrapped_json_array_candidate(candidate: dict[str, Any]) -> list[Any] | None:
    if len(candidate) != 1:
        return None
    key, value = next(iter(candidate.items()))
    if key not in _PROPOSAL_ARRAY_WRAPPER_KEYS or not isinstance(value, list):
        return None
    return value


def _is_standalone_json_value(raw: str, start: int, end: int) -> bool:
    previous_position = _previous_non_whitespace_index(raw, start)
    previous = raw[previous_position] if previous_position is not None else None
    if previous in _EMBEDDED_JSON_PREFIXES and not (
        previous == ":" and (
            "\n" in raw[previous_position + 1 : start]
            or _has_plaintext_array_label(raw, previous_position, start)
        )
    ):
        return False

    following = _next_non_whitespace(raw, end)
    if following in _EMBEDDED_JSON_SUFFIXES:
        return False

    return True


def _previous_non_whitespace_index(raw: str, index: int) -> int | None:
    for position in range(index - 1, -1, -1):
        if not raw[position].isspace():
            return position
    return None


def _previous_non_whitespace(raw: str, index: int) -> str | None:
    position = _previous_non_whitespace_index(raw, index)
    if position is not None:
        return raw[position]
    return None


def _has_plaintext_array_label(raw: str, previous_position: int, start: int) -> bool:
    line_start = raw.rfind("\n", 0, previous_position) + 1
    prefix = raw[line_start:start].strip()
    return bool(_OUTPUT_ARRAY_LABEL_RE.fullmatch(prefix))


def _next_non_whitespace(raw: str, index: int) -> str | None:
    for position in range(index, len(raw)):
        if not raw[position].isspace():
            return raw[position]
    return None


def _require_field(item: dict[str, Any], key: str, index: int) -> Any:
    if key not in item:
        raise LlmOutputError(f"proposal {index} missing {key}")
    return item[key]


def _require_list(item: dict[str, Any], key: str, index: int) -> list[Any]:
    value = _require_field(item, key, index)
    if not isinstance(value, list):
        raise LlmOutputError(f"proposal {index} {key} must be a list")
    return value


def _require_non_empty_string(item: dict[str, Any], key: str, index: int) -> str:
    value = _require_field(item, key, index)
    if not isinstance(value, str) or not value.strip():
        raise LlmOutputError(f"proposal {index} {key} must be a non-empty string")
    return value.strip()


def _require_string_list(item: dict[str, Any], key: str, index: int) -> tuple[str, ...]:
    values = _require_list(item, key, index)
    for value_index, value in enumerate(values):
        if not isinstance(value, str):
            raise LlmOutputError(f"proposal {index} {key}[{value_index}] must be a string")
    return tuple(values)


def _require_int_list(item: dict[str, Any], key: str, index: int) -> tuple[int, ...]:
    values = _require_list(item, key, index)
    for value_index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int):
            raise LlmOutputError(f"proposal {index} {key}[{value_index}] must be an int")
    return tuple(values)


def _validate_relation(relation: Any) -> dict[str, Any] | None:
    """Return a normalized relation, or None if it is malformed/unsupported.

    Lenient: the caller drops a None edge rather than failing the whole proposal,
    so a single bad/typo'd relation (e.g. ``mentations``) does not nuke an
    otherwise-good atom.
    """
    if not isinstance(relation, dict):
        return None

    relation_type = relation.get("type")
    if relation_type == "relates_to":
        if set(relation) != {"type", "target_title"}:
            return None
        target_title = relation.get("target_title")
        if not isinstance(target_title, str) or not target_title.strip():
            return None
        return {"type": "relates_to", "target_title": target_title.strip()}

    if relation_type == "mentions":
        if set(relation) != {"type", "entity"}:
            return None
        entity = relation.get("entity")
        if not isinstance(entity, str) or not entity.strip():
            return None
        return {"type": "mentions", "entity": entity.strip()}

    return None


def _build_proposal(
    item: Any, index: int, allowed_projects: set[str], seen_titles: set[str]
) -> SliceProposal:
    """Build one proposal. Hard-field violations raise (caller skips this proposal);
    soft issues (unknown project, malformed relation) are repaired in place."""
    if not isinstance(item, dict):
        raise LlmOutputError(f"proposal {index} is not an object")
    unknown_keys = sorted(set(item) - _PROPOSAL_KEYS)
    if unknown_keys:
        raise LlmOutputError(f"proposal {index} has unknown fields: {', '.join(unknown_keys)}")

    artifact_kind = item.get("artifact_kind")
    if artifact_kind not in ARTIFACT_KINDS:
        raise LlmOutputError(f"proposal {index} has invalid artifact_kind: {artifact_kind}")

    title = _require_non_empty_string(item, "title", index)
    if title in seen_titles:
        raise LlmOutputError(f"proposal {index} has duplicate title: {title}")
    body = item.get("body")
    if not isinstance(body, str) or not body.strip():
        raise LlmOutputError(f"proposal {index} has empty body")

    tags = _require_string_list(item, "tags", index)
    source_fragment_indices = _require_int_list(item, "source_fragment_indices", index)
    if not source_fragment_indices:
        raise LlmOutputError(f"proposal {index} source_fragment_indices must not be empty")
    relations = _require_list(item, "relations", index)

    # Soft: coerce an unknown/unsafe project to _unknown rather than dropping the atom.
    project = item.get("project")
    if not isinstance(project, str) or project not in allowed_projects or not is_safe_path_component(project):
        _LOG.warning("atomize: proposal %s project %r not allowed; coerced to _unknown", index, project)
        project = "_unknown"

    # Soft: drop malformed/unsupported relation edges, keep the valid ones.
    validated_relations: list[dict[str, Any]] = []
    for relation_index, relation in enumerate(relations):
        normalized = _validate_relation(relation)
        if normalized is None:
            _LOG.warning("atomize: proposal %s dropped invalid relation %s", index, relation_index)
        else:
            validated_relations.append(normalized)

    seen_titles.add(title)
    return SliceProposal(
        title=title,
        artifact_kind=artifact_kind,
        project=project,
        tags=tags,
        body=body,
        source_fragment_indices=source_fragment_indices,
        relations=tuple(validated_relations),
    )


def _parse_proposals(data: Any, known_projects: list[str]) -> list[SliceProposal]:
    if not isinstance(data, list):
        raise LlmOutputError("agent output must be a JSON array")
    if not data:
        return []

    allowed_projects = set(known_projects) | {"_unknown"}
    proposals: list[SliceProposal] = []
    seen_titles: set[str] = set()
    dropped: list[str] = []
    for index, item in enumerate(data):
        try:
            proposals.append(_build_proposal(item, index, allowed_projects, seen_titles))
        except LlmOutputError as exc:
            dropped.append(str(exc))
            _LOG.warning("atomize: dropped proposal %s: %s", index, exc)

    if not proposals:
        raise LlmOutputError("no salvageable proposals: " + "; ".join(dropped))
    return proposals


def parse(raw: str, known_projects: list[str]) -> list[SliceProposal]:
    last_error: LlmOutputError | None = None
    valid_proposals: list[list[SliceProposal]] = []
    seen_spans: set[tuple[int, int]] = set()

    for data, start, end in _iter_json_arrays(raw):
        span = (start, end)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        try:
            valid_proposals.append(_parse_proposals(data, known_projects))
        except LlmOutputError as exc:
            last_error = exc

    if len(valid_proposals) > 1:
        raise LlmOutputError("multiple valid JSON arrays found in agent output")

    if valid_proposals:
        return valid_proposals[0]

    if last_error is not None:
        raise last_error

    raise LlmOutputError("no JSON array found in agent output")
