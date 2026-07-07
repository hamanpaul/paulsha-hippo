"""Memory consumption telemetry helpers (#148). Pure functions, no IO.

DEPRECATED signal path: extract_cited / extract_matched were the original `used`
detectors. They are NON-NORMATIVE as of the consumption-loop change — they could
only fire on an agent echoing an opaque 16-hex id or a verbatim title, which drove
false 0s. The `used` signal is now read-based (PostToolUse(Read), capability
stage2-memory-read-attribution). These remain only for backward-compat and may be
removed in a follow-up. extract_offered is retained for compatibility/tooling.
"""

from __future__ import annotations

import re
from typing import Iterable

# Assumes canonical lowercase 16-hex slice ids (production slice_id is "sl-" + sha[:16]).
_SLICE_ID = re.compile(r"sl-[0-9a-f]{16}")
# Pipe (and title) optional: producers emit bare [[stem--sl-id]] for empty-title slices.
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_MATCH_MIN_TITLE = 8


def extract_offered(brief: str) -> list[tuple[str, str]]:
    """Extract (slice_id, title) pairs from a brief's [[stem--sl-id|title]] wikilinks."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for target, title in _WIKILINK.findall(brief):
        m = _SLICE_ID.search(target)
        if not m or m.group(0) in seen:
            continue
        seen.add(m.group(0))
        out.append((m.group(0), (title or "").strip()))
    return out


def extract_cited(assistant_text: str, offered_ids: Iterable[str]) -> set[str]:
    """DEPRECATED (non-normative): slice ids the agent echoed (in [[..]] or bare) that
    were offered. No longer the `used` signal — see module docstring."""
    offered = set(offered_ids)
    return {sid for sid in _SLICE_ID.findall(assistant_text or "") if sid in offered}


def extract_matched(
    assistant_text: str, offered: Iterable[tuple[str, str]], *, exclude: Iterable[str] = ()
) -> set[str]:
    """DEPRECATED (non-normative): offered ids whose (>=8 char) title appears verbatim in
    assistant text, minus exclude. No longer the `used` signal — see module docstring."""
    text = assistant_text or ""
    skip = set(exclude)
    out: set[str] = set()
    for sid, title in offered:
        t = (title or "").strip()
        if sid in skip or len(t) < _MATCH_MIN_TITLE:
            continue
        if t in text:  # intentional recall-favoring substring heuristic (not token-boundary)
            out.add(sid)
    return out
