from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from paulsha_hippo.lib.lifecycle import schema as stage3
from .config import AtomizerConfig
from .provenance import safe_provenance
from .splitter import Fragment

if TYPE_CHECKING:
    from .llm_output import SliceProposal

_T4_FIELDS = ("memory_layer", "source_agent", "captured_at", "provenance", "supersedes")
# Stage 3 ordered fields first, then T4 + provenance handled specially in render().
_SCALAR_ORDER = (
    "phase", "project", "slice_id", "artifact_kind", "version", "created_at",
    "created_by", "source_session", "gate_required", "checksum",
    "memory_layer", "source_agent", "captured_at", "supersedes",
    "distilled_from", "fragment_ref", "session_title", "title", "atom_title", "tags", "source_fragments", "publication_id",
)


@dataclass(frozen=True)
class Slice:
    slice_id: str
    frontmatter: dict[str, object]
    body: str
    title: str | None = None
    relations: tuple[dict[str, object], ...] = ()


def _slice_id(fragment: Fragment) -> str:
    body_hash = hashlib.sha256(fragment.body.encode("utf-8")).hexdigest()
    key = (
        f"{fragment.project}|{fragment.source_agent}|{fragment.source_session}|"
        f"{fragment.fragment_index}|{body_hash}"
    )
    return "sl-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def normalize_tags(value: object) -> list[str]:
    """正規化任意來源的 tags 值成 MOC index 要求的 list[str]（issue #101）。

    生產實證：cg 蒸餾 70-slice 大 session 產出未引號 issue 編號 tag（``264``），
    寫入 YAML frontmatter 後成為 int；``moc/search.py::_tags_fts_text`` 與
    ``moc/census.py::_census_tags_invalid`` 對 tags 型別做嚴格 list[str] 驗證
    （這是刻意的最後防線，見兩者 docstring），一旦命中非字串元素就整個 slice
    判 invalid_frontmatter 排除——`build_index()` 的 warning 因此讓
    orchestrator 的 ``clean = not warnings`` 每輪誤判 partial。

    規則（元素層級，遇非 list 整體視為空 list）：
    - ``str``：保留原樣，但 ``strip()`` 後為空（含純空白）則丟棄。
    - ``bool`` / ``int`` / ``float``：以 ``str()`` 字串化保留。
    - 其他型別（``None``、``dict``、嵌套 ``list``/``tuple`` 等非 scalar）：丟棄。
    正規化後保序去重（字串化後的值相同即視為同一 tag，例如 ``264`` 與
    ``"264"``）。
    """
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in value:
        text: str | None
        if isinstance(item, str):
            text = item if item.strip() else None
        elif isinstance(item, (bool, int, float)):
            text = str(item)
        else:
            text = None
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def build(fragment: Fragment, config: AtomizerConfig) -> Slice:
    body = fragment.body
    artifact_kind = config.artifact_kind_map.get(fragment.source_artifact, config.default_artifact_kind)
    phase = config.phase_map.get(artifact_kind, config.default_phase)
    slice_id = _slice_id(fragment)
    session_ref = f"{fragment.source_agent}:{fragment.source_session}"
    fragment_ref = f"{fragment.source_agent}__{fragment.source_session}__{fragment.fragment_index:03d}"
    frontmatter: dict[str, object] = {
        # Stage 3 required
        "phase": phase,
        "project": fragment.project,
        "slice_id": slice_id,
        "artifact_kind": artifact_kind,
        "version": "1",
        "created_at": fragment.captured_at,
        "created_by": fragment.source_agent,
        "source_session": fragment.source_session,
        "gate_required": False,
        "checksum": stage3.compute_checksum(body),
        # T4 read contract
        "memory_layer": "knowledge",
        "source_agent": fragment.source_agent,
        "captured_at": fragment.captured_at,
        "provenance": dict(fragment.provenance),
        "supersedes": [],
        # derivation
        "distilled_from": session_ref,
        "fragment_ref": fragment_ref,
        "session_title": fragment.session_title,
        "title": fragment.session_title or "",
        "atom_title": fragment.session_title or "",
        "distiller": {},
    }
    return Slice(slice_id=slice_id, frontmatter=frontmatter, body=body, title=fragment.session_title or None)


def _phase_for_artifact_kind(artifact_kind: str) -> str:
    phase_map = {
        "research": "research",
        "spec": "define",
        "plan": "plan",
        "report": "review",
        "review": "review",
    }
    return phase_map.get(artifact_kind, "review")


def build_from_proposal(proposal: "SliceProposal", session_meta: dict[str, object]) -> Slice:
    body = proposal.body
    agent = str(session_meta["source_agent"])
    session = str(session_meta["source_session"])
    captured_at = str(session_meta["captured_at"])
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    slice_id = "sl-" + hashlib.sha256(f"{agent}|{session}|{body_hash}".encode("utf-8")).hexdigest()[:16]
    frontmatter: dict[str, object] = {
        "phase": _phase_for_artifact_kind(proposal.artifact_kind),
        "project": proposal.project,
        "slice_id": slice_id,
        "artifact_kind": proposal.artifact_kind,
        "version": "1",
        "created_at": captured_at,
        "created_by": agent,
        "source_session": session,
        "gate_required": False,
        "checksum": stage3.compute_checksum(body),
        "memory_layer": "knowledge",
        "source_agent": agent,
        "captured_at": captured_at,
        "provenance": dict(session_meta.get("provenance") or {}),
        "distiller": safe_provenance(dict(session_meta.get("distiller") or {})),
        "supersedes": [],
        "distilled_from": f"{agent}:{session}",
        "session_title": str(session_meta.get("session_title", "")),
        "title": proposal.title,
        "atom_title": proposal.title,
        "tags": normalize_tags(list(proposal.tags)),
        "source_fragments": list(proposal.source_fragment_indices),
    }
    return Slice(
        slice_id=slice_id,
        frontmatter=frontmatter,
        body=body,
        title=proposal.title,
        relations=tuple(proposal.relations),
    )


def validate(frontmatter: dict[str, object], body: str) -> list[str]:
    result = stage3.validate_frontmatter(frontmatter=frontmatter, body=body)
    errors = list(result.errors)
    for field in _T4_FIELDS:
        if field not in frontmatter:
            errors.append(f"missing T4 contract field: {field}")
    if "distiller" not in frontmatter:
        errors.append("missing distiller provenance")
    if frontmatter.get("memory_layer") != "knowledge":
        errors.append("memory_layer must be 'knowledge'")
    return errors


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def render(slice_: Slice) -> str:
    fm = slice_.frontmatter
    lines = ["---"]
    for key in _SCALAR_ORDER:
        if key not in fm:
            continue
        if key in ("project", "session_title", "title", "atom_title", "created_at", "captured_at", "source_session"):
            # free-text → always a quoted scalar so YAML indicator chars can't deform it
            lines.append(f"{key}: {json.dumps(str(fm[key]), ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {_scalar(fm[key])}")
    provenance = fm.get("provenance") or {}
    if isinstance(provenance, dict):
        lines.append("provenance:")
        for pkey in ("repo", "commit", "path"):
            lines.append(f"  {pkey}: {json.dumps(str(provenance.get(pkey, '')), ensure_ascii=False)}")
    distiller = fm.get("distiller") or {}
    if isinstance(distiller, dict):
        lines.append("distiller:")
        for key in (
            "profile_id", "profile_revision", "tier", "attempt_index",
            "requested_model", "requested_effort", "observed_model",
            "model_verification", "command_fingerprint", "fallback_reason",
            "config_hash", "skill_hash", "hippo_version", "build_commit", "response_schema",
            "attempts", "chunk_provenance",
            "elapsed_seconds", "failure_category", "stderr", "exit_code",
        ):
            if key not in distiller:
                continue
            value = distiller[key]
            if value is None:
                rendered = "null"
            elif key in ("attempts", "chunk_provenance"):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            elif isinstance(value, (bool, int, float)):
                rendered = _scalar(value)
            else:
                rendered = json.dumps(str(value), ensure_ascii=False)
            lines.append(f"  {key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n" + slice_.body
