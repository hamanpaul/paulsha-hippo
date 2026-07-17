"""Deterministic 32K/12K prompt budgeting with complete fragment coverage."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from .splitter import Fragment

CONTEXT_WINDOW = 32_768
MAX_INPUT_TOKENS = 12_000
INPUT_SAFETY_MARGIN = 0.10
SAFE_INPUT_TOKENS = int(MAX_INPUT_TOKENS * (1.0 - INPUT_SAFETY_MARGIN))
MAX_OUTPUT_TOKENS = 2_048
MAX_PROMPT_ARGV_BYTES = 48 * 1024


class ContextBudgetExceeded(ValueError):
    """The fixed prompt or one deterministic part cannot satisfy both gates."""


@dataclass(frozen=True)
class FragmentPart:
    original_fragment_index: int
    part_index: int
    part_count: int
    body: str
    fragment: Fragment

    def as_fragment(self) -> Fragment:
        return replace(
            self.fragment,
            body=self.body,
            part_index=self.part_index,
            part_count=self.part_count,
        )


@dataclass(frozen=True)
class PromptChunk:
    index: int
    count: int
    prompt: str
    estimated_tokens: int
    parts: tuple[FragmentPart, ...]


def estimate_tokens(text: str) -> int:
    """Stable conservative-enough estimator independent of provider libraries."""
    return math.ceil(len(text.encode("utf-8")) / 4)


def provider_context_supported(context_window: int) -> bool:
    return context_window >= CONTEXT_WINDOW


def _render(skill_text: str, parts: list[FragmentPart], known_projects: list[str]) -> str:
    from .prompt import build_prompt

    return build_prompt(skill_text, [part.as_fragment() for part in parts], known_projects)


def _fits(prompt: str, *, max_input_tokens: int, max_prompt_argv_bytes: int) -> bool:
    safe_tokens = int(max_input_tokens * (1.0 - INPUT_SAFETY_MARGIN))
    return (
        estimate_tokens(prompt) <= safe_tokens
        and len(prompt.encode("utf-8")) <= max_prompt_argv_bytes
    )


def _max_prefix_that_fits(
    text: str,
    *,
    template: Fragment,
    skill_text: str,
    known_projects: list[str],
    max_input_tokens: int,
    max_prompt_argv_bytes: int,
) -> int:
    low, high, best = 1, len(text), 0
    while low <= high:
        middle = (low + high) // 2
        candidate = FragmentPart(
            original_fragment_index=template.fragment_index,
            part_index=1,
            part_count=9999,
            body=text[:middle],
            fragment=template,
        )
        if _fits(
            _render(skill_text, [candidate], known_projects),
            max_input_tokens=max_input_tokens,
            max_prompt_argv_bytes=max_prompt_argv_bytes,
        ):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _split_fragment(
    fragment: Fragment,
    *,
    skill_text: str,
    known_projects: list[str],
    max_input_tokens: int,
    max_prompt_argv_bytes: int,
) -> list[FragmentPart]:
    provisional = FragmentPart(
        original_fragment_index=fragment.fragment_index,
        part_index=1,
        part_count=1,
        body=fragment.body,
        fragment=fragment,
    )
    if _fits(
        _render(skill_text, [provisional], known_projects),
        max_input_tokens=max_input_tokens,
        max_prompt_argv_bytes=max_prompt_argv_bytes,
    ):
        return [provisional]

    # Keep every delimiter attached to its preceding unit.  Parts are exact,
    # contiguous source spans, so concatenating them reconstructs the original
    # fragment byte-for-byte even with repeated/leading/trailing blank lines.
    boundaries = [match.end() for match in re.finditer(r"\n\n", fragment.body)]
    starts = [0, *boundaries]
    ends = [*boundaries, len(fragment.body)]
    paragraphs = [
        fragment.body[start:end]
        for start, end in zip(starts, ends)
        if start < end
    ]
    bodies: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = current + paragraph
        probe = FragmentPart(
            original_fragment_index=fragment.fragment_index,
            part_index=1,
            part_count=9999,
            body=candidate,
            fragment=fragment,
        )
        if _fits(
            _render(skill_text, [probe], known_projects),
            max_input_tokens=max_input_tokens,
            max_prompt_argv_bytes=max_prompt_argv_bytes,
        ):
            current = candidate
            continue
        if current:
            bodies.append(current)
            current = ""
        remaining = paragraph
        while remaining:
            size = _max_prefix_that_fits(
                remaining,
                template=fragment,
                skill_text=skill_text,
                known_projects=known_projects,
                max_input_tokens=max_input_tokens,
                max_prompt_argv_bytes=max_prompt_argv_bytes,
            )
            if size <= 0:
                raise ContextBudgetExceeded("context_budget_exceeded: fixed prompt leaves no fragment budget")
            piece, remaining = remaining[:size], remaining[size:]
            if remaining:
                bodies.append(piece)
            else:
                current = piece
    if current or not bodies:
        bodies.append(current)

    count = len(bodies)
    return [
        FragmentPart(
            original_fragment_index=fragment.fragment_index,
            part_index=index,
            part_count=count,
            body=body,
            fragment=fragment,
        )
        for index, body in enumerate(bodies, start=1)
    ]


def pack_prompt_chunks(
    *,
    skill_text: str,
    fragments: list[Fragment],
    known_projects: list[str],
    context_window: int = CONTEXT_WINDOW,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    max_prompt_argv_bytes: int = MAX_PROMPT_ARGV_BYTES,
) -> list[PromptChunk]:
    if not provider_context_supported(context_window):
        raise ContextBudgetExceeded(
            f"context_budget_exceeded: provider context {context_window} < {CONTEXT_WINDOW}"
        )
    if max_input_tokens > MAX_INPUT_TOKENS or max_prompt_argv_bytes > MAX_PROMPT_ARGV_BYTES:
        raise ContextBudgetExceeded("context_budget_exceeded: configured prompt gate exceeds contract")
    if not fragments:
        return []

    parts = [
        part
        for fragment in fragments
        for part in _split_fragment(
            fragment,
            skill_text=skill_text,
            known_projects=known_projects,
            max_input_tokens=max_input_tokens,
            max_prompt_argv_bytes=max_prompt_argv_bytes,
        )
    ]
    groups: list[list[FragmentPart]] = []
    current: list[FragmentPart] = []
    for part in parts:
        candidate = [*current, part]
        prompt = _render(skill_text, candidate, known_projects)
        if _fits(
            prompt,
            max_input_tokens=max_input_tokens,
            max_prompt_argv_bytes=max_prompt_argv_bytes,
        ):
            current = candidate
            continue
        if not current:
            raise ContextBudgetExceeded("context_budget_exceeded: fragment part exceeds prompt gates")
        groups.append(current)
        current = [part]
    if current:
        groups.append(current)

    count = len(groups)
    chunks: list[PromptChunk] = []
    for index, group in enumerate(groups, start=1):
        rendered = _render(skill_text, group, known_projects)
        if not _fits(
            rendered,
            max_input_tokens=max_input_tokens,
            max_prompt_argv_bytes=max_prompt_argv_bytes,
        ):
            raise ContextBudgetExceeded("context_budget_exceeded: rendered chunk exceeds prompt gates")
        chunks.append(
            PromptChunk(
                index=index,
                count=count,
                prompt=rendered,
                estimated_tokens=estimate_tokens(rendered),
                parts=tuple(group),
            )
        )
    return chunks
