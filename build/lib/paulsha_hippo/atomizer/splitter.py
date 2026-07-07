"""Deterministic heading splitter for atomizer."""
import re
from dataclasses import dataclass
from typing import Any

from .config import AtomizerConfig


@dataclass(frozen=True)
class Fragment:
    """Fragment metadata and body."""
    project: str
    source_agent: str
    source_session: str
    source_artifact: str
    captured_at: str
    provenance: dict[str, str]
    fragment_index: int
    body: str
    session_title: str = ""


def split(body: str, config: AtomizerConfig) -> list[str]:
    """Deterministically segment a session body into fragment texts.
    
    Args:
        body: Session body text
        config: Atomizer configuration
        
    Returns:
        List of fragment text strings
    """
    # Return empty list for empty/whitespace-only input
    if not body or not body.strip():
        return []
    
    # Compile boundary patterns
    boundary_regexes = [re.compile(pattern, re.MULTILINE) for pattern in config.boundary_patterns]
    
    # Find all boundary matches
    lines = body.split('\n')
    boundary_indices = []
    
    for i, line in enumerate(lines):
        for regex in boundary_regexes:
            if regex.match(line):
                boundary_indices.append(i)
                break
    
    # If no boundaries, return single fragment
    if not boundary_indices:
        text = body.strip()
        if not text:
            return []
        return _enforce_max_chars(text, config.max_fragment_chars)
    
    # Split by boundaries, preserving heading line with its fragment
    fragments = []
    start_idx = 0
    boundary_index_set = set(boundary_indices)
    
    for boundary_idx in boundary_indices:
        if boundary_idx > start_idx:
            # Content before this boundary
            segment = '\n'.join(lines[start_idx:boundary_idx]).strip()
            if segment:
                fragments.extend(
                    _enforce_max_chars(
                        segment,
                        config.max_fragment_chars,
                        preserve_first_line=start_idx in boundary_index_set,
                    )
                )
        start_idx = boundary_idx
    
    # Final segment from last boundary to end
    if start_idx < len(lines):
        segment = '\n'.join(lines[start_idx:]).strip()
        if segment:
            fragments.extend(
                _enforce_max_chars(
                    segment,
                    config.max_fragment_chars,
                    preserve_first_line=start_idx in boundary_index_set,
                )
            )
    
    return fragments


def _enforce_max_chars(
    text: str,
    limit: int,
    *,
    preserve_first_line: bool = False,
) -> list[str]:
    """Split text into chunks respecting character limit.
    
    Args:
        text: Text to split
        limit: Maximum characters per chunk
        
    Returns:
        List of text chunks, each <= limit characters
    """
    if limit <= 0 or len(text) <= limit:
        return [text]

    if preserve_first_line:
        first_line, sep, rest = text.partition('\n')
        if not sep:
            return _enforce_max_chars(text, limit)

        prefix = first_line + '\n'
        available = limit - len(prefix)
        if available <= 0:
            return _enforce_max_chars(text, limit)

        chunks = []
        for chunk in _enforce_max_chars(rest, available):
            combined = (prefix + chunk).strip()
            if combined:
                chunks.append(combined)
        return chunks
    
    chunks = []
    remaining = text
    
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining.strip())
            break
        
        # Find last newline before limit
        chunk = remaining[:limit]
        last_newline = chunk.rfind('\n')
        
        if last_newline > 0:
            # Split at newline
            chunks.append(remaining[:last_newline].strip())
            remaining = remaining[last_newline + 1:]
        else:
            # No newline, split at limit
            chunks.append(remaining[:limit].strip())
            remaining = remaining[limit:]
    
    return [c for c in chunks if c]
