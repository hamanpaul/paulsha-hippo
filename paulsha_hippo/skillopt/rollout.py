from __future__ import annotations

from collections.abc import Callable, Sequence

from paulsha_hippo.atomizer.agent_exec import AgentClient
from paulsha_hippo.atomizer.config import AtomizerConfig, load_config
from paulsha_hippo.atomizer.llm_promoter import LLMPromoter
from paulsha_hippo.atomizer.slice_frontmatter import Slice
from paulsha_hippo.atomizer.splitter import Fragment


def make_atomize_rollout(
    agent_client: AgentClient,
    known_projects: Sequence[str],
    *,
    config: AtomizerConfig | None = None,
) -> Callable[[str, list[Fragment]], list[Slice]]:
    cfg = config
    if cfg is None:
        cfg, _ = load_config()

    projects = list(known_projects)

    def rollout(skill_text: str, fragments: list[Fragment]) -> list[Slice]:
        if not fragments:
            return []

        promoter = LLMPromoter(
            agent_client,
            skill_text,
            projects,
            model=cfg.agent_exec_model,
        )
        return promoter.promote(fragments, cfg)

    return rollout
