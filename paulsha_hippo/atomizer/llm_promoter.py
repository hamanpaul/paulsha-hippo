from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import replace

from . import llm_output, prompt, slice_frontmatter
from .agent_exec import AgentClient, AgentExecError, CachingAgentClient
from .config import AtomizerConfig, is_safe_path_component
from .promoter import Promoter
from .slice_frontmatter import Slice
from .splitter import Fragment

_LOG = logging.getLogger("paulsha_hippo.atomizer")


class PromoteError(Exception):
    """Raised when session-level promotion cannot complete safely."""


class LLMPromoter(Promoter):
    _CACHE_HASH_RE = re.compile(r"[0-9a-f]{64}")

    def __init__(
        self,
        agent_client: AgentClient,
        skill_text: str,
        known_projects: list[str],
        *,
        model: str = "unknown",
    ) -> None:
        self._agent = agent_client
        self._skill = skill_text
        self._projects = list(known_projects)
        self._model = model

    @staticmethod
    def _fragments_hash(fragments: list[Fragment]) -> str:
        joined = "\0".join(
            f"{fragment.fragment_index}:{fragment.body}"
            for fragment in sorted(fragments, key=lambda fragment: fragment.fragment_index)
        )
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    @classmethod
    def cache_key_for_fragments(cls, fragments: list[Fragment]) -> str:
        if not fragments:
            raise PromoteError("llm promote failed: cannot build cache key for empty fragment list")
        first = fragments[0]
        session_key = f"{first.source_agent}:{first.source_session}"
        return f"{session_key}__{cls._fragments_hash(fragments)}"

    @classmethod
    def is_valid_cache_key(cls, cache_key: str) -> bool:
        session_key, separator, fragments_hash = cache_key.rpartition("__")
        if not separator:
            return False
        agent, colon, session = session_key.partition(":")
        return (
            bool(colon)
            and all(is_safe_path_component(value) for value in (agent, session))
            and bool(cls._CACHE_HASH_RE.fullmatch(fragments_hash))
        )

    def clear_cache_for_fragments(self, fragments: list[Fragment]) -> None:
        if not isinstance(self._agent, CachingAgentClient) or not fragments:
            return
        self._agent.clear_cache_key(self.cache_key_for_fragments(fragments))

    def promote(self, fragments: list[Fragment], config: AtomizerConfig) -> list[Slice]:
        del config
        if isinstance(fragments, Fragment):
            raise PromoteError("llm promote failed: expected per-session fragment list")
        if not fragments:
            return []

        first = fragments[0]
        session_signature = (
            first.project,
            first.source_agent,
            first.source_session,
            first.captured_at,
            dict(first.provenance),
        )
        for fragment in fragments[1:]:
            if (
                fragment.project,
                fragment.source_agent,
                fragment.source_session,
                fragment.captured_at,
                dict(fragment.provenance),
            ) != session_signature:
                raise PromoteError("llm promote failed: fragments must belong to one session")

        valid_fragment_indices = {fragment.fragment_index for fragment in fragments}
        session_meta = {
            "source_agent": first.source_agent,
            "source_session": first.source_session,
            "captured_at": first.captured_at,
            "provenance": dict(first.provenance),
            "session_title": first.session_title,
        }
        built_prompt = prompt.build_prompt(self._skill, fragments, self._projects)

        try:
            if isinstance(self._agent, CachingAgentClient):
                raw = self._agent.run_cached(
                    built_prompt,
                    self.cache_key_for_fragments(fragments),
                )
            else:
                raw = self._agent.run(built_prompt)
            proposals = llm_output.parse(raw, self._projects)
        except (AgentExecError, llm_output.LlmOutputError) as exc:
            raise PromoteError(f"llm promote failed: {exc}") from exc

        slices: list[Slice] = []
        for proposal in proposals:
            unknown_indices = sorted(set(proposal.source_fragment_indices) - valid_fragment_indices)
            if unknown_indices:
                # gemma4 does not reliably honour the fragment-index contract; drop the
                # out-of-range references (lenient) instead of failing the whole session.
                _LOG.warning(
                    "atomize: dropped out-of-range source_fragment_indices %s for session %s:%s",
                    unknown_indices, first.source_agent, first.source_session,
                )
                kept = tuple(i for i in proposal.source_fragment_indices if i in valid_fragment_indices)
                if not kept:
                    # every reference was bogus; a slice still needs >=1 source fragment,
                    # so attribute the atom to the whole session rather than dropping it.
                    kept = tuple(sorted(valid_fragment_indices))
                proposal = replace(proposal, source_fragment_indices=kept)
            slice_ = slice_frontmatter.build_from_proposal(proposal, session_meta)
            errors = slice_frontmatter.validate(slice_.frontmatter, slice_.body)
            if errors:
                raise PromoteError(f"slice validation failed: {errors}")
            slices.append(slice_)
        return slices
