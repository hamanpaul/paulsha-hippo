from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_WORD = re.compile(r"[0-9a-z_]+", re.IGNORECASE)
_STRICT_FLOAT = re.compile(r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*\Z")
_WEIGHTS = {
    "granularity": 0.35,
    "boundary": 0.35,
    "one_concept": 0.20,
    "relation": 0.10,
}
_JUDGE_PROMPT = """You are scoring atomization quality for knowledge slices.
Judge ONLY atomization quality, NOT project assignment.
Consider:
- granularity: slices should not be too coarse or too fragmented
- concept boundaries: each slice should focus on one clear idea
- one-concept-per-slice: avoid mixing unrelated topics together
- relation soundness: relations should be present when helpful and not form isolated islands

Reference examples of good atomization for this domain:
{reference}
These references are rubric examples only. Do not treat them as target outputs to match.

Candidate slices:
{candidate}

Return ONLY a single float between 0 and 1.
"""


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD.findall(text or "")}


def _slice_text(slice_: Any) -> str:
    title = getattr(slice_, "title", None) or ""
    body = getattr(slice_, "body", "") or ""
    return f"{title} {body}"


def _body_length(slice_: Any) -> int:
    return len((getattr(slice_, "body", "") or "").strip())


def _granularity_balance(output: list[Any], gold: dict[str, Any]) -> float:
    del gold
    count = len(output)
    if count == 0:
        return 0.0
    if 2 <= count <= 6:
        count_score = 1.0
    elif count == 1:
        count_score = 0.5
    else:
        count_score = max(0.0, 1.0 - ((count - 6) / 6.0))

    mean_body_length = sum(_body_length(slice_) for slice_ in output) / count
    if mean_body_length <= 0:
        length_score = 0.0
    elif mean_body_length < 80.0:
        length_score = mean_body_length / 80.0
    elif mean_body_length > 1600.0:
        length_score = 1600.0 / mean_body_length
    else:
        length_score = 1.0
    return 0.6 * count_score + 0.4 * length_score


def _concept_boundary_clarity(output: list[Any], gold: dict[str, Any]) -> float:
    del gold
    if not output:
        return 0.0
    token_sets = [_tokens(_slice_text(slice_)) for slice_ in output]
    if len(token_sets) == 1:
        return 0.5 if token_sets[0] else 0.0

    overlaps: list[float] = []
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1 :]:
            union = left | right
            if not union:
                overlaps.append(1.0)
                continue
            overlaps.append(len(left & right) / len(union))
    if not overlaps:
        return 1.0
    return 1.0 - (sum(overlaps) / len(overlaps))


def _one_concept_per_slice(output: list[Any], gold: dict[str, Any]) -> float:
    del gold
    if not output:
        return 0.0
    focused = 0
    for slice_ in output:
        title = (getattr(slice_, "title", None) or "").strip()
        body = (getattr(slice_, "body", "") or "").strip()
        if title and body and len(body) <= 1800:
            focused += 1
    return focused / len(output)


def _relation_presence(output: list[Any], gold: dict[str, Any]) -> float:
    del gold
    if not output:
        return 0.0
    with_relations = sum(1 for slice_ in output if getattr(slice_, "relations", ()))
    return with_relations / len(output)


def structural_score(output: list[Any], gold: dict[str, Any]) -> float:
    parts = {
        "granularity": _granularity_balance(output, gold),
        "boundary": _concept_boundary_clarity(output, gold),
        "one_concept": _one_concept_per_slice(output, gold),
        "relation": _relation_presence(output, gold),
    }
    return sum(_WEIGHTS[name] * parts[name] for name in _WEIGHTS)


def _format_slices(output: list[Any]) -> str:
    return "\n".join(
        f"- {getattr(slice_, 'title', None) or '(untitled)'}: {(getattr(slice_, 'body', '') or '')[:300]}"
        for slice_ in output
    ) or "(none)"


def _format_reference(gold: dict[str, Any]) -> str:
    references = gold.get("reference_slices", [])
    return "\n".join(
        f"- {reference.get('title', '')}: {str(reference.get('body', ''))[:300]}"
        for reference in references
    ) or "(none)"


def _parse_float(raw: str) -> float:
    match = _STRICT_FLOAT.fullmatch(raw or "")
    if match is None:
        raise ValueError(f"judge must return exactly one float: {raw!r}")
    value = float(match.group(1))
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"judge float must be between 0 and 1: {raw!r}")
    return value


def _judge_enabled(gold: dict[str, Any]) -> bool:
    judge = gold.get("judge")
    if isinstance(judge, dict) and "enabled" in judge:
        return bool(judge["enabled"])
    if "judge_enabled" in gold:
        return bool(gold["judge_enabled"])
    return True


def make_hybrid_score(judge_client: Any, *, alpha: float = 0.4) -> Callable[[list[Any], dict[str, Any]], float]:
    alpha = float(alpha)
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be between 0 and 1 inclusive, got {alpha!r}")

    def score(output: list[Any], gold: dict[str, Any]) -> float:
        structural = structural_score(output, gold)
        if not _judge_enabled(gold):
            return structural
        prompt = _JUDGE_PROMPT.format(
            reference=_format_reference(gold),
            candidate=_format_slices(output),
        )
        judge = _parse_float(judge_client.run(prompt))
        return alpha * structural + (1.0 - alpha) * judge

    return score
