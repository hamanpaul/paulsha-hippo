from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import replace

from . import budget, llm_output, slice_frontmatter
from .agent_exec import AgentClient, AgentExecError, AgentUnavailableError, CachingAgentClient
from .config import AtomizerConfig, is_safe_path_component
from .promoter import Promoter
from .slice_frontmatter import Slice
from .splitter import Fragment

_LOG = logging.getLogger("paulsha_hippo.atomizer")


class PromoteError(Exception):
    """Raised when session-level promotion cannot complete safely.

    category ∈ {"backend_unavailable", "transient", "invalid_output",
    "context_budget_exceeded"}（#15 失敗分類）。
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_output",
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.attempts = attempts


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, AgentUnavailableError):
        return "backend_unavailable"
    if isinstance(exc, AgentExecError):
        return "transient"
    return "invalid_output"


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
        self.last_disposition = "findings"
        self.no_findings_reasons: tuple[str, ...] = ()
        self._last_chunk_cache_keys: tuple[str, ...] = ()
        self.last_raw_output = ""

    @staticmethod
    def _fragments_hash(fragments: list[Fragment]) -> str:
        joined = "\0".join(
            f"{fragment.fragment_index}:{fragment.part_index}/{fragment.part_count}:{fragment.body}"
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
    def cache_key_for_prompt(cls, fragments: list[Fragment], prompt_text: str) -> str:
        """Bind transient LLM cache to the complete rendered prompt contract."""
        if not fragments:
            raise PromoteError("llm promote failed: cannot cache an empty chunk")
        first = fragments[0]
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        return f"{first.source_agent}:{first.source_session}__{prompt_hash}"

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

    def clear_last_chunk_caches(self) -> None:
        if not isinstance(self._agent, CachingAgentClient):
            return
        for cache_key in self._last_chunk_cache_keys:
            self._agent.clear_cache_key(cache_key)

    @property
    def last_chunk_cache_keys(self) -> tuple[str, ...]:
        return self._last_chunk_cache_keys

    def _run_chunk(
        self,
        prompt_text: str,
        chunk_fragments: list[Fragment],
        attempts: int,
    ) -> llm_output.ParsedResponse:
        cache_key = self.cache_key_for_prompt(chunk_fragments, prompt_text)
        # Never let a failure before stdout assignment inherit another chunk's
        # private output as parked evidence.
        self.last_raw_output = ""
        last_error: Exception | None = None
        attempted = 0
        for attempt in range(1, attempts + 1):
            attempted = attempt
            try:
                if isinstance(self._agent, CachingAgentClient):
                    raw = self._agent.run_cached(prompt_text, cache_key)
                else:
                    raw = self._agent.run(prompt_text)
                self.last_raw_output = raw
                return llm_output.parse_response(raw, self._projects)
            except (AgentExecError, llm_output.LlmOutputError) as exc:
                last_error = exc
                if isinstance(exc, AgentUnavailableError):
                    break
                if isinstance(exc, llm_output.LlmOutputError) and isinstance(
                    self._agent, CachingAgentClient
                ):
                    self._agent.clear_cache_key(cache_key)
                if attempt >= attempts:
                    break
        assert last_error is not None
        raise PromoteError(
            f"llm promote failed after {attempted} chunk attempt(s): {last_error}",
            category=_failure_category(last_error),
            attempts=attempted,
        ) from last_error

    def promote(self, fragments: list[Fragment], config: AtomizerConfig) -> list[Slice]:
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

        try:
            chunks = budget.pack_prompt_chunks(
                skill_text=self._skill,
                fragments=fragments,
                known_projects=self._projects,
                context_window=config.context_window,
                max_input_tokens=config.max_input_tokens,
                max_prompt_argv_bytes=config.max_prompt_argv_bytes,
            )
        except budget.ContextBudgetExceeded as exc:
            raise PromoteError(str(exc), category="context_budget_exceeded") from exc

        valid_fragment_indices = {fragment.fragment_index for fragment in fragments}
        session_meta = {
            "source_agent": first.source_agent,
            "source_session": first.source_session,
            "captured_at": first.captured_at,
            "provenance": dict(first.provenance),
            "session_title": first.session_title,
        }
        responses: list[llm_output.ParsedResponse] = []
        chunk_cache_keys: list[str] = []
        for chunk in chunks:
            chunk_fragments = [part.as_fragment() for part in chunk.parts]
            chunk_cache_keys.append(self.cache_key_for_prompt(chunk_fragments, chunk.prompt))
            responses.append(
                self._run_chunk(chunk.prompt, chunk_fragments, config.chunk_retries)
            )
        self._last_chunk_cache_keys = tuple(chunk_cache_keys)
        reasons = tuple(
            response.reason
            for response in responses
            if response.disposition == "no_findings" and response.reason is not None
        )
        if responses and all(response.disposition == "no_findings" for response in responses):
            self.last_disposition = "no_findings"
            self.no_findings_reasons = reasons
            return []
        self.last_disposition = "findings"
        self.no_findings_reasons = reasons
        proposals: list[llm_output.SliceProposal] = []
        seen_findings: set[tuple[object, ...]] = set()
        for response in responses:
            for proposal in response.findings:
                if (
                    first.project != "_unknown"
                    and first.project in self._projects
                    and proposal.project != first.project
                ):
                    _LOG.warning(
                        "atomize: model project %s overridden by pinned source project %s for %s:%s",
                        proposal.project,
                        first.project,
                        first.source_agent,
                        first.source_session,
                    )
                    proposal = replace(proposal, project=first.project)
                dedup_key = (
                    proposal.project,
                    proposal.title,
                    proposal.artifact_kind,
                    proposal.body,
                    proposal.tags,
                    proposal.source_fragment_indices,
                    tuple(
                        sorted(
                            json.dumps(relation, sort_keys=True, separators=(",", ":"))
                            for relation in proposal.relations
                        )
                    ),
                )
                if dedup_key in seen_findings:
                    continue
                seen_findings.add(dedup_key)
                proposals.append(proposal)

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
