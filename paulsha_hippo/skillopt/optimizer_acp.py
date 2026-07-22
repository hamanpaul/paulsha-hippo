"""SkillOpt optimizer bound to the canonical external-agent router.

The historical module name is retained for import compatibility.  The
production optimizer no longer starts an ACP bridge or constructs a provider
argv; it submits the frozen prompt to ``ExternalAgentRouter``, which owns
stdin delivery, minimal environment construction, bounded fallback, and
failure evidence.  The optional ``runner`` is intentionally injection-only
for unit tests and offline callers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from paulsha_hippo.agent_profiles import ExternalAgentRouter


_PROMPT_TEMPLATE = """You are optimizing a reusable agent SKILL.md (a trainable instruction document).

Current skill:
<<<SKILL
{skill}
SKILL

A model following this skill scored low on these cases (input / expected / got / score):
{failures}

Propose exactly ONE bounded edit (add, replace, or delete a small section) that should
improve performance on such cases. Keep the skill's structure and frontmatter intact.
Return ONLY the complete edited SKILL.md, with no commentary and no code fences.
"""


def _format_failures(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return "(none)"

    lines: list[str] = []
    for failure in failures:
        lines.append(
            "- input: {inp!r}\n  expected: {gold!r}\n  got: {out!r}\n  score: {score}".format(
                inp=failure.get("input"),
                gold=failure.get("gold"),
                out=failure.get("output"),
                score=failure.get("score"),
            )
        )
    return "\n".join(lines)


def _normalize_skill_text(text: str) -> str:
    return text.rstrip("\n") + "\n"


def make_router_optimizer(
    router: ExternalAgentRouter | None = None,
    *,
    runner: Callable[[str], str] | None = None,
) -> Callable[[str, list[dict[str, Any]]], str]:
    """Create the SkillOpt optimizer using one bounded external-agent path.

    ``runner`` exists only as an explicit test seam.  Exactly one of
    ``router`` or ``runner`` must be supplied; production callers must provide
    the canonical router.
    """
    if (router is None) == (runner is None):
        raise ValueError("provide exactly one of router or runner")

    def optimizer(skill_text: str, failures: list[dict[str, Any]]) -> str:
        prompt = _PROMPT_TEMPLATE.format(
            skill=skill_text,
            failures=_format_failures(failures),
        )
        edited = router.run(prompt) if router is not None else runner(prompt)
        return _normalize_skill_text(edited)

    return optimizer


def make_acp_optimizer(
    router: ExternalAgentRouter | None = None,
    *,
    runner: Callable[[str], str] | None = None,
) -> Callable[[str, list[dict[str, Any]]], str]:
    """Compatibility alias; production callers should use ``make_router_optimizer``."""
    return make_router_optimizer(router, runner=runner)


__all__ = ["make_router_optimizer", "make_acp_optimizer"]
