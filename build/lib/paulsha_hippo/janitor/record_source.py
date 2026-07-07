"""
Knowledge record source reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    import json


MAX_RECORD_FILE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class KnowledgeRecord:
    """A knowledge layer record."""
    record_id: str
    supersedes: tuple[str, ...]
    source_key: str
    captured_at: str
    provenance: Mapping[str, str]
    path: Path
    title: str = ""
    project: str = ""


def _parse_frontmatter(text: str) -> Mapping[str, Any] | None:
    """Parse YAML/JSON frontmatter from text."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    
    # Find closing ---
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    
    block = "\n".join(lines[1:end])
    if not block.strip():
        return None
    
    # Parse YAML if available, else JSON
    if HAS_YAML:
        try:
            data = yaml.safe_load(block)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
    else:
        # Fallback to JSON only if block starts with '{'
        if block.strip().startswith("{"):
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    return data
            except Exception:
                return None
    
    return None


def _clean_string(value: Any) -> str | None:
    """Return stripped string metadata values, rejecting non-strings and empty values."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _build_record(path: Path, data: Mapping[str, Any]) -> tuple[KnowledgeRecord | None, str | None]:
    """Build KnowledgeRecord from frontmatter data."""
    # Skip non-knowledge layers silently
    if data.get("memory_layer") != "knowledge":
        return None, None
    
    # Require slice_id
    slice_id = _clean_string(data.get("slice_id"))
    if not slice_id:
        return None, f"{path}: missing slice_id"
    
    # Parse supersedes
    supersedes_raw = data.get("supersedes", [])
    if isinstance(supersedes_raw, str):
        cleaned = _clean_string(supersedes_raw)
        supersedes = (cleaned,) if cleaned else ()
    elif isinstance(supersedes_raw, list):
        supersedes = tuple(
            cleaned
            for item in supersedes_raw
            if (cleaned := _clean_string(item)) is not None
        )
    else:
        supersedes = ()
    
    # Build source_key
    agent = _clean_string(data.get("source_agent")) or "_unknown"
    session = _clean_string(data.get("source_session")) or "_unknown"
    source_key = f"{agent}:{session}"
    
    # Extract captured_at
    captured_at = _clean_string(data.get("captured_at")) or ""

    # Advisory fields for hygiene lint.
    title = _clean_string(data.get("title")) or ""
    project = _clean_string(data.get("project")) or ""
    
    # Extract provenance
    prov_raw = data.get("provenance", {})
    if isinstance(prov_raw, dict):
        provenance = MappingProxyType({
            k: cleaned for k, v in prov_raw.items()
            if k in ("repo", "commit", "path")
            and isinstance(v, str)
            and (cleaned := _clean_string(v)) is not None
        })
    else:
        provenance = MappingProxyType({})
    
    record = KnowledgeRecord(
        record_id=slice_id,
        supersedes=supersedes,
        source_key=source_key,
        captured_at=captured_at,
        provenance=provenance,
        path=path,
        title=title,
        project=project,
    )
    return record, None


def iter_records(knowledge_root: Path) -> tuple[list[KnowledgeRecord], list[str]]:
    """
    Iterate over knowledge records in a directory.
    
    Returns:
        tuple of (records, warnings)
    """
    if not knowledge_root.exists():
        return [], []
    
    records: list[KnowledgeRecord] = []
    warnings: list[str] = []
    seen_record_ids: set[str] = set()
    
    for md_path in sorted(knowledge_root.rglob("*.md")):
        if md_path.is_symlink():
            warnings.append(f"{md_path}: symlink not allowed; skipped")
            continue
        try:
            file_size = md_path.stat().st_size
        except OSError:
            warnings.append(f"{md_path}: could not stat file")
            continue
        if file_size > MAX_RECORD_FILE_BYTES:
            warnings.append(f"{md_path}: file too large ({file_size} bytes); skipped")
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            warnings.append(f"{md_path}: could not read file")
            continue
        
        data = _parse_frontmatter(text)
        if data is None:
            warnings.append(f"{md_path}: unparseable frontmatter; skipped")
            continue
        
        record, warning = _build_record(md_path, data)
        if warning:
            warnings.append(warning)
        if record:
            if record.record_id in seen_record_ids:
                warnings.append(f"{md_path}: duplicate slice_id {record.record_id!r}; skipped")
                continue
            seen_record_ids.add(record.record_id)
            records.append(record)
    
    return records, warnings
