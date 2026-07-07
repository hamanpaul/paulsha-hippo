from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re


PHASES = (
    "research",
    "define",
    "plan",
    "build",
    "verify",
    "review",
    "ship",
)

ARTIFACT_KINDS = (
    "research",
    "spec",
    "roadmap",
    "test",
    "task",
    "todo",
    "plan",
    "report",
    "review",
    "ship-record",
    "gate-report",
)

REQUIRED_FRONTMATTER_FIELDS = (
    "phase",
    "project",
    "slice_id",
    "artifact_kind",
    "version",
    "created_at",
    "created_by",
    "source_session",
    "gate_required",
    "checksum",
)

_FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class ArtifactDocument:
    frontmatter: dict[str, object]
    body: str


@dataclass(frozen=True)
class FrontmatterValidationResult:
    ok: bool
    errors: tuple[str, ...]


def parse_artifact_text(text: str) -> ArtifactDocument:
    matched = _FRONTMATTER_PATTERN.match(text)
    if not matched:
        raise ValueError("artifact frontmatter 缺失或格式錯誤")
    frontmatter_raw = matched.group(1)
    body = matched.group(2)
    frontmatter: dict[str, object] = {}
    for line in frontmatter_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter line 無法解析: {line}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("frontmatter key 不可為空")
        frontmatter[key] = _parse_frontmatter_value(key, raw_value.strip())
    return ArtifactDocument(frontmatter=frontmatter, body=body)


def validate_frontmatter(*, frontmatter: dict[str, object], body: str) -> FrontmatterValidationResult:
    errors: list[str] = []
    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in frontmatter or frontmatter[field] in (None, ""):
            errors.append(f"frontmatter 必填欄位缺失: {field}")

    phase = frontmatter.get("phase")
    if phase not in PHASES:
        errors.append(f"phase 必須為 {PHASES} 之一")

    artifact_kind = frontmatter.get("artifact_kind")
    if artifact_kind not in ARTIFACT_KINDS:
        errors.append(f"artifact_kind 必須為 {ARTIFACT_KINDS} 之一")

    gate_required = frontmatter.get("gate_required")
    if not isinstance(gate_required, bool):
        errors.append("gate_required 必須是布林值")

    created_at = frontmatter.get("created_at")
    if not _is_iso8601(created_at):
        errors.append("created_at 必須是 ISO8601 時間")

    checksum = frontmatter.get("checksum")
    if not isinstance(checksum, str):
        errors.append("checksum 必須是字串")
    elif checksum != compute_checksum(body):
        errors.append("checksum 驗證失敗")

    return FrontmatterValidationResult(ok=not errors, errors=tuple(errors))


def compute_checksum(body: str) -> str:
    return sha256(body.encode("utf-8")).hexdigest()


def _parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _parse_frontmatter_value(key: str, value: str) -> object:
    if key == "tags":
        parsed = _parse_bracket_list(value)
        if parsed is not None:
            normalized: list[str] = []
            for item in parsed:
                if not isinstance(item, str):
                    break
                normalized.append(item)
            else:
                return normalized
    if key == "source_fragments":
        parsed = _parse_bracket_list(value)
        if parsed is not None:
            numbers: list[int] = []
            for item in parsed:
                if type(item) is int:
                    numbers.append(item)
                    continue
                if isinstance(item, str):
                    try:
                        numbers.append(int(item))
                    except ValueError:
                        break
                    continue
                break
            else:
                return numbers
    return _parse_scalar(value)


def _parse_bracket_list(value: str) -> list[object] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_strip_legacy_quotes(part.strip()) for part in inner.split(",")]


def _strip_legacy_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _is_iso8601(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True
