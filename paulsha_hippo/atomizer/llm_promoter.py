from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import replace

from . import budget, llm_output, slice_frontmatter
from ..agent_profiles import (
    AgentRunError,
    AgentRunResult,
    ExternalAgentRouter,
    RESPONSE_SCHEMA_VERSION,
)
from .agent_exec import (
    AgentClient,
    AgentExecError,
    AgentTransientError,
    AgentUnavailableError,
    CachingAgentClient,
)
from .provenance import provenance_from_result, safe_provenance, sha256_text
from .config import AtomizerConfig, is_safe_path_component, is_valid_project_id
from ..noise import is_generic_title
from .promoter import Promoter
from .slice_frontmatter import Slice
from .splitter import Fragment

_LOG = logging.getLogger("paulsha_hippo.atomizer")

_PROMOTE_FAILURE_CATEGORIES = {
    "transient": "transient",
    "process": "transient",
    "timeout": "transient",
    "transport": "transient",
    "rate_limit": "transient",
    "quota": "transient",
    "capacity": "transient",
    "invalid_output": "invalid_output",
    "empty_output": "invalid_output",
    "ineligible": "backend_unavailable",
    "auth": "backend_unavailable",
    "context_capability": "backend_unavailable",
    "policy": "backend_unavailable",
    "config": "backend_unavailable",
    "schema": "backend_unavailable",
    "unsafe": "backend_unavailable",
    "budget": "backend_unavailable",
}

_GROUNDING_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}")
_GROUNDING_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_GROUNDING_STOP_WORDS = {
    "and", "are", "for", "from", "into", "one", "only", "that", "the",
    "their", "then", "this", "use", "with",
}
_OUTPUT_CONTRACT_MARKERS = {
    "disposition", "findings", "json", "markdown", "no_findings",
    "reason", "schema_version",
}


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
    if isinstance(exc, AgentRunError):
        return _PROMOTE_FAILURE_CATEGORIES.get(exc.category, "backend_unavailable")
    if isinstance(exc, AgentUnavailableError):
        return "backend_unavailable"
    if isinstance(exc, AgentExecError):
        category = str(getattr(exc, "category", "transient") or "transient")
        # AgentTransientError is the legacy typed wrapper used by callers that
        # do not provide a more specific subprocess category. Keep the
        # fine-grained process category in attempts/provenance while exposing
        # the pipeline's coarse transient category at this boundary.
        if isinstance(exc, AgentTransientError) and category == "process":
            category = "transient"
        return _PROMOTE_FAILURE_CATEGORIES.get(category, "backend_unavailable")
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
        config_hash: str = "",
        response_schema: str = RESPONSE_SCHEMA_VERSION,
    ) -> None:
        self._agent = agent_client
        self._skill = skill_text
        self._projects = list(known_projects)
        self._model = model
        self._config_hash = config_hash
        self._response_schema = response_schema
        self.last_disposition = "findings"
        self.no_findings_reasons: tuple[str, ...] = ()
        self._last_chunk_cache_keys: tuple[str, ...] = ()
        self.last_raw_output = ""
        self.last_provenance: dict[str, object] = {}

    @staticmethod
    def _fragments_hash(fragments: list[Fragment]) -> str:
        joined = "\0".join(
            f"{fragment.fragment_index}:{fragment.part_index}/{fragment.part_count}:{fragment.body}"
            for fragment in sorted(fragments, key=lambda fragment: fragment.fragment_index)
        )
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    @classmethod
    def cache_key_for_fragments(
        cls,
        fragments: list[Fragment],
        *,
        response_schema: str = RESPONSE_SCHEMA_VERSION,
    ) -> str:
        if not fragments:
            raise PromoteError("llm promote failed: cannot build cache key for empty fragment list")
        first = fragments[0]
        session_key = f"{first.source_agent}:{first.source_session}"
        bound_hash = hashlib.sha256(
            json.dumps(
                {
                    "fragments_hash": cls._fragments_hash(fragments),
                    "response_schema": response_schema,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"{session_key}__{bound_hash}"

    @classmethod
    def cache_key_for_prompt(
        cls,
        fragments: list[Fragment],
        prompt_text: str,
        *,
        response_schema: str = RESPONSE_SCHEMA_VERSION,
    ) -> str:
        """Bind transient LLM cache to the complete rendered prompt contract."""
        if not fragments:
            raise PromoteError("llm promote failed: cannot cache an empty chunk")
        first = fragments[0]
        prompt_hash = hashlib.sha256(
            json.dumps(
                {"prompt": prompt_text, "response_schema": response_schema},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"{first.source_agent}:{first.source_session}__{prompt_hash}"

    def _router(self) -> ExternalAgentRouter | None:
        candidate = self._agent
        if isinstance(candidate, CachingAgentClient):
            candidate = candidate._inner
        return candidate if isinstance(candidate, ExternalAgentRouter) else None

    @staticmethod
    def _grounding_units(value: object) -> set[str]:
        text = str(value or "")
        units = {
            token.casefold()
            for token in _GROUNDING_WORD_RE.findall(text)
            if token.casefold() not in _GROUNDING_STOP_WORDS
        }
        for run in _GROUNDING_CJK_RE.findall(text):
            units.update(run[index:index + 2] for index in range(len(run) - 1))
        return units

    @classmethod
    def _validate_grounding(
        cls,
        response: llm_output.ParsedResponse,
        fragments: list[Fragment],
    ) -> llm_output.ParsedResponse:
        if response.disposition != "findings":
            return response
        fragments_by_index: dict[int, list[Fragment]] = {}
        for fragment in fragments:
            fragments_by_index.setdefault(fragment.fragment_index, []).append(fragment)
        for index, proposal in enumerate(response.findings):
            referenced = [
                fragment
                for fragment_index in proposal.source_fragment_indices
                if fragment_index in fragments_by_index
                for fragment in fragments_by_index[fragment_index]
            ]
            # Preserve the existing lenient out-of-range attribution contract:
            # when every model-supplied index is invalid, the whole session is
            # the only honest source authority available.
            if not referenced:
                referenced = list(fragments)
            source_text = "\n".join(fragment.body for fragment in referenced)
            # Tags are model-selected metadata, not source evidence.  Counting
            # them would let an unrelated body pass by copying one source noun
            # into a tag.
            proposal_text = "\n".join((proposal.title, proposal.body))
            source_units = cls._grounding_units(source_text)
            proposal_units = cls._grounding_units(proposal_text)
            leaked_contract = (
                proposal_units & _OUTPUT_CONTRACT_MARKERS
            ) - source_units
            if len(leaked_contract) >= 3 or not (source_units & proposal_units):
                raise llm_output.LlmOutputError(
                    f"proposal {index} not grounded in declared source fragments"
                )
        return response

    def _known_projects_for_fragments(self, fragments: list[Fragment]) -> list[str]:
        projects = list(self._projects)
        if fragments:
            source_project = fragments[0].project
            if (
                source_project != "_unknown"
                and is_valid_project_id(source_project)
                and source_project not in projects
            ):
                projects.append(source_project)
        return projects

    def _response_validator(self, fragments: list[Fragment]):
        known_projects = self._known_projects_for_fragments(fragments)

        def validate(raw: str) -> llm_output.ParsedResponse:
            response = llm_output.parse_response(raw, known_projects)
            return self._validate_grounding(response, fragments)

        return validate

    def _router_result(self) -> AgentRunResult | None:
        result = getattr(self._agent, "last_result", None)
        if result is None and isinstance(self._agent, CachingAgentClient):
            result = getattr(self._agent._inner, "last_result", None)
        return result if isinstance(result, AgentRunResult) else None

    def _router_attempts(self) -> tuple[AgentRunResult, ...]:
        router = self._router()
        if router is not None:
            return tuple(
                attempt
                for attempt in getattr(router, "attempts", ())
                if isinstance(attempt, AgentRunResult)
            )
        return ()

    def _cache_namespace(self) -> str:
        provider = getattr(self._agent, "cache_namespace", None)
        return str(provider()) if callable(provider) else ""

    def _bound_cache_key_for_prompt(self, fragments: list[Fragment], prompt_text: str) -> str:
        if not fragments:
            raise PromoteError("llm promote failed: cannot cache an empty chunk")
        if not self._cache_namespace() and not self._config_hash:
            return self.cache_key_for_prompt(
                fragments, prompt_text, response_schema=self._response_schema
            )
        first = fragments[0]
        payload = {
            "operation": "atomization",
            "prompt": prompt_text,
            "response_schema": self._response_schema,
            "agent_namespace": self._cache_namespace(),
            "config_hash": self._config_hash,
            "skill_hash": sha256_text(self._skill),
        }
        prompt_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"{first.source_agent}:{first.source_session}__{prompt_hash}"

    def _bound_cache_key_for_fragments(self, fragments: list[Fragment]) -> str:
        if not fragments:
            raise PromoteError("llm promote failed: cannot build cache key for empty fragment list")
        if not self._cache_namespace() and not self._config_hash:
            return self.cache_key_for_fragments(
                fragments, response_schema=self._response_schema
            )
        prompt_hash = self._fragments_hash(fragments)
        first = fragments[0]
        payload = {
            "operation": "atomization-session",
            "fragments_hash": prompt_hash,
            "response_schema": self._response_schema,
            "agent_namespace": self._cache_namespace(),
            "config_hash": self._config_hash,
            "skill_hash": sha256_text(self._skill),
        }
        bound_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"{first.source_agent}:{first.source_session}__{bound_hash}"

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
        self._agent.clear_cache_key(self._bound_cache_key_for_fragments(fragments))
        self.clear_last_chunk_caches()

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
        cache_key = self._bound_cache_key_for_prompt(chunk_fragments, prompt_text)
        # Never let a failure before stdout assignment inherit another chunk's
        # private output as parked evidence.
        self.last_raw_output = ""
        last_error: Exception | None = None
        attempted = 0
        validator = self._response_validator(chunk_fragments)
        router = self._router()
        for attempt in range(1, attempts + 1):
            attempted = attempt
            try:
                if isinstance(self._agent, CachingAgentClient):
                    raw = self._agent.run_cached(
                        prompt_text,
                        cache_key,
                        response_validator=validator if router is not None else None,
                        response_schema=self._response_schema if router is not None else None,
                    )
                elif router is not None:
                    raw = router.run(prompt_text, response_validator=validator)
                else:
                    raw = self._agent.run(prompt_text)
                self.last_raw_output = raw
                router_result = self._router_result()
                if router_result is not None:
                    self.last_provenance = safe_provenance(
                        provenance_from_result(
                            router_result,
                            config_hash=self._config_hash,
                            skill_hash=sha256_text(self._skill),
                            attempts=self._router_attempts(),
                        )
                    )
                return validator(raw)
            except (AgentExecError, AgentRunError, llm_output.LlmOutputError) as exc:
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

    def _repair_generic_title(
        self,
        proposal: llm_output.SliceProposal,
        *,
        cache_namespace: str,
        proposal_index: int,
        config: AtomizerConfig,
    ) -> llm_output.SliceProposal:
        """Perform at most one title-only repair for a generic proposal.

        The repair is deliberately a separate cache namespace.  Replaying the
        full proposal cache would otherwise keep returning the same generic
        title forever while making the repair appear to have run.
        """
        if not is_generic_title(proposal.title):
            return proposal
        repair_prompt = (
            "只輸出一個具體的繁體中文知識原子標題，最多 20 字、單行、不要標點。\n"
            f"原標題：{proposal.title}\n"
            f"原子內容：{proposal.body[:1200]}"
        )
        config_identity = self._config_hash or sha256_text(
            json.dumps(
                {
                    "context_window": config.context_window,
                    "max_input_tokens": config.max_input_tokens,
                    "max_prompt_argv_bytes": config.max_prompt_argv_bytes,
                },
                sort_keys=True,
            )
        )
        cache_key = "title-repair__" + sha256_text(
            json.dumps(
                {
                    "session_atom_cache_key": cache_namespace,
                    "proposal_index": proposal_index,
                    "original_title": proposal.title,
                    "response_schema": self._response_schema,
                    "prompt_hash": sha256_text(repair_prompt),
                    "config_hash": config_identity,
                    "skill_hash": sha256_text(self._skill),
                },
                sort_keys=True,
            )
        )
        try:
            if isinstance(self._agent, CachingAgentClient):
                repaired_raw = self._agent.run_cached(repair_prompt, cache_key)
            else:
                repaired_raw = self._agent.run(repair_prompt)
            repaired = self._title_from_repair_output(repaired_raw)
            if not repaired or is_generic_title(repaired):
                raise PromoteError(
                    "generic title repair did not produce a specific title",
                    category="invalid_output",
                    attempts=1,
                )
            return replace(proposal, title=repaired)
        except PromoteError:
            raise
        except Exception as exc:
            raise PromoteError(
                f"generic title repair failed: {type(exc).__name__}",
                category=_failure_category(exc),
                attempts=1,
            ) from exc

    @staticmethod
    def _title_from_repair_output(value: object) -> str:
        """Accept the bounded title contract without treating JSON as prose.

        External CLIs occasionally wrap a one-field response in the normal
        proposal JSON shape even when the repair prompt asks for a scalar.
        Parsing that shape here prevents a JSON object from becoming a false
        semantic title while keeping the repair strictly title-only.
        """
        raw = str(value or "").strip()
        candidate: object = raw
        if raw.startswith(("{", "[", '"')):
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, str):
                candidate = parsed
            elif isinstance(parsed, dict):
                candidate = parsed.get("title", "")
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                candidate = parsed[0].get("title", "")
        return re.sub(r"\s+", " ", str(candidate or "").strip())[:20]

    def _run_session(
        self,
        chunks,
    ) -> tuple[list[llm_output.ParsedResponse], tuple[str, ...]] | None:
        """Use the router's session transaction when the client supports it."""
        router = self._router()
        if router is None or not callable(getattr(router, "run_session", None)):
            return None
        prompts = tuple(chunk.prompt for chunk in chunks)
        base_keys = tuple(
            self._bound_cache_key_for_prompt(
                [part.as_fragment() for part in chunk.parts], chunk.prompt
            )
            for chunk in chunks
        )
        session_fragments = [
            part.as_fragment()
            for chunk in chunks
            for part in chunk.parts
        ]
        validator = self._response_validator(session_fragments)
        self.last_raw_output = ""
        try:
            if isinstance(self._agent, CachingAgentClient):
                raw_outputs = self._agent.run_session(
                    prompts,
                    cache_keys=base_keys,
                    response_validator=validator,
                    response_schema=self._response_schema,
                )
                cache_keys = tuple(self._agent.last_cache_keys or base_keys)
            else:
                raw_outputs = router.run_session(
                    prompts,
                    response_validator=validator,
                )
                cache_keys = base_keys
            responses: list[llm_output.ParsedResponse] = []
            for raw in raw_outputs:
                self.last_raw_output = raw
                responses.append(validator(raw))
            router_result = self._router_result()
            if router_result is not None:
                self.last_provenance = safe_provenance(
                    provenance_from_result(
                        router_result,
                        config_hash=self._config_hash,
                        skill_hash=sha256_text(self._skill),
                        attempts=self._router_attempts(),
                    )
                )
            return responses, cache_keys
        except (AgentExecError, AgentRunError, llm_output.LlmOutputError) as exc:
            raise PromoteError(
                f"llm promote failed after session attempt(s): {exc}",
                category=_failure_category(exc),
                attempts=len(self._router_attempts()),
            ) from exc

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
                known_projects=self._known_projects_for_fragments(fragments),
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
        session_result = self._run_session(chunks)
        if session_result is not None:
            responses, session_cache_keys = session_result
            chunk_cache_keys.extend(session_cache_keys)
        else:
            for chunk in chunks:
                chunk_fragments = [part.as_fragment() for part in chunk.parts]
                chunk_cache_keys.append(
                    self._bound_cache_key_for_prompt(chunk_fragments, chunk.prompt)
                )
                responses.append(
                    self._run_chunk(chunk.prompt, chunk_fragments, config.chunk_retries)
                )
        session_meta["distiller"] = self.last_provenance or safe_provenance(
            provenance_from_result(
                None,
                config_hash=self._config_hash,
                skill_hash=sha256_text(self._skill),
            )
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
                    and is_valid_project_id(first.project)
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
                proposal = self._repair_generic_title(
                    proposal,
                    cache_namespace=self._bound_cache_key_for_fragments(fragments),
                    proposal_index=len(proposals),
                    config=config,
                )
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
                # External CLIs do not always honour the fragment-index contract; drop the
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
