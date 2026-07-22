from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.agent_profiles import AgentProfile, ExternalAgentRouter
from paulsha_hippo.atomizer import agent_exec
from paulsha_hippo.atomizer import llm_promoter
from paulsha_hippo.atomizer.agent_exec import FakeAgentClient
from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.atomizer.splitter import Fragment

CFG = AtomizerConfig(
    schema_version="1",
    boundary_patterns=(r"^#{1,6}\s",),
    max_fragment_chars=8000,
    artifact_kind_map={},
    phase_map={},
    default_artifact_kind="report",
    default_phase="review",
)

_TWO = (
    '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":["x"],'
    '"body":"body a","source_fragment_indices":[0],"relations":[]},'
    '{"title":"b","artifact_kind":"plan","project":"paulshaclaw","tags":[],'
    '"body":"body b","source_fragment_indices":[1],"relations":[]}]'
)
_MERGE = (
    '[{"title":"m","artifact_kind":"report","project":"paulshaclaw","tags":[],'
    '"body":"merged","source_fragment_indices":[0,1],"relations":[]}]'
)


_WITH_RELATIONS = (
    '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":["x"],'
    '"body":"body a","source_fragment_indices":[0],"relations":'
    '[{"type":"mentions","entity":"MTK"}]},'
    '{"title":"b","artifact_kind":"plan","project":"paulshaclaw","tags":[],'
    '"body":"body b","source_fragment_indices":[1],"relations":'
    '[{"type":"relates_to","target_title":"a"}]}]'
)
_OUT_OF_RANGE = (
    '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],'
    '"body":"body a","source_fragment_indices":[99],"relations":[]}]'
)


def _frag(index: int) -> Fragment:
    return Fragment(
        project="paulshaclaw",
        source_agent="claude",
        source_session="s1",
        source_artifact="research",
        captured_at="2026-06-02T00:00:00Z",
        provenance={"repo": "r", "commit": "c", "path": "p"},
        fragment_index=index,
        body=f"b{index}",
    )


def _frag_with(**overrides: object) -> Fragment:
    base = _frag(0)
    data = {
        "project": base.project,
        "source_agent": base.source_agent,
        "source_session": base.source_session,
        "source_artifact": base.source_artifact,
        "captured_at": base.captured_at,
        "provenance": dict(base.provenance),
        "fragment_index": base.fragment_index,
        "body": base.body,
    }
    data.update(overrides)
    return Fragment(**data)


def _promoter(canned: str) -> llm_promoter.LLMPromoter:
    return llm_promoter.LLMPromoter(
        FakeAgentClient(canned),
        skill_text="SKILL",
        known_projects=["paulshaclaw"],
    )


class LLMPromoterTests(unittest.TestCase):
    def test_two_slices(self):
        slices = _promoter(_TWO).promote([_frag(0), _frag(1)], CFG)
        self.assertEqual(len(slices), 2)

    def test_merge_two_fragments_into_one_slice(self):
        slices = _promoter(_MERGE).promote([_frag(0), _frag(1)], CFG)
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0].frontmatter["source_fragments"], [0, 1])

    def test_relations_are_preserved(self):
        slices = _promoter(_WITH_RELATIONS).promote([_frag(0), _frag(1)], CFG)
        self.assertEqual(slices[0].relations, ({"type": "mentions", "entity": "MTK"},))
        self.assertEqual(slices[1].relations, ({"type": "relates_to", "target_title": "a"},))

    def test_known_source_project_overrides_model_rehoming(self):
        response = (
            '[{"title":"a","artifact_kind":"report","project":"other-project",'
            '"tags":[],"body":"body a","source_fragment_indices":[0],"relations":[]}]'
        )
        promoter = llm_promoter.LLMPromoter(
            FakeAgentClient(response),
            skill_text="SKILL",
            known_projects=["paulshaclaw", "other-project"],
        )

        slices = promoter.promote([_frag(0)], CFG)

        self.assertEqual(slices[0].frontmatter["project"], "paulshaclaw")

    def test_invalid_output_fails_closed(self):
        with self.assertRaises(llm_promoter.PromoteError):
            _promoter("garbage not json").promote([_frag(0)], CFG)

    def test_empty_legacy_array_fails_closed(self):
        with self.assertRaises(llm_promoter.PromoteError):
            _promoter("[]").promote([_frag(0)], CFG)

    def test_explicit_no_findings_returns_no_slices(self):
        promoter = _promoter(
            '{"schema_version":1,"disposition":"no_findings",'
            '"reason":"only acknowledgements","findings":[]}'
        )
        self.assertEqual(promoter.promote([_frag(0)], CFG), [])
        self.assertEqual(promoter.last_disposition, "no_findings")

    def test_bad_artifact_kind_fails_closed(self):
        bad = (
            '[{"title":"a","artifact_kind":"nope","project":"paulshaclaw","tags":[],'
            '"body":"b","source_fragment_indices":[0],"relations":[]}]'
        )
        with self.assertRaises(llm_promoter.PromoteError):
            _promoter(bad).promote([_frag(0)], CFG)

    def test_out_of_range_source_fragment_index_falls_back_to_whole_session(self):
        # gemma4 stochastically references indices that do not exist; intersect with
        # the valid set rather than nuking the whole (otherwise good) session. When
        # every reference is bogus, attribute to the whole session (a slice needs >=1).
        slices = _promoter(_OUT_OF_RANGE).promote([_frag(0)], CFG)
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0].frontmatter["source_fragments"], [0])

    def test_partial_out_of_range_indices_intersected(self):
        partial = (
            '[{"title":"a","artifact_kind":"report","project":"paulshaclaw","tags":[],'
            '"body":"body a","source_fragment_indices":[0,99],"relations":[]}]'
        )
        slices = _promoter(partial).promote([_frag(0), _frag(1)], CFG)
        self.assertEqual(slices[0].frontmatter["source_fragments"], [0])

    def test_mixed_session_input_fails_closed(self):
        fragments = [_frag(0), _frag_with(fragment_index=1, source_session="s2")]
        with self.assertRaises(llm_promoter.PromoteError):
            _promoter(_TWO).promote(fragments, CFG)

    def test_cached_output_is_bound_to_complete_prompt_contract(self):
        calls = {"n": 0}

        class Counting(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                calls["n"] += 1
                return _TWO

        with tempfile.TemporaryDirectory() as tmp:
            cached = agent_exec.CachingAgentClient(Counting(), Path(tmp))
            fragments = [_frag(0), _frag(1)]

            llm_promoter.LLMPromoter(
                cached,
                skill_text="SKILL-A",
                known_projects=["paulshaclaw"],
            ).promote(fragments, CFG)
            llm_promoter.LLMPromoter(
                cached,
                skill_text="SKILL-B",
                known_projects=["paulshaclaw", "other-project"],
            ).promote(fragments, CFG)

        self.assertEqual(calls["n"], 2)

    def test_cache_key_changes_when_fragment_index_mapping_changes(self):
        fragments_a = [
            _frag_with(fragment_index=0, body="alpha"),
            _frag_with(fragment_index=1, body="beta"),
        ]
        fragments_b = [
            _frag_with(fragment_index=0, body="beta"),
            _frag_with(fragment_index=1, body="alpha"),
        ]

        self.assertNotEqual(
            llm_promoter.LLMPromoter.cache_key_for_fragments(fragments_a),
            llm_promoter.LLMPromoter.cache_key_for_fragments(fragments_b),
        )

    def test_non_session_input_fails_closed(self):
        with self.assertRaises(llm_promoter.PromoteError):
            _promoter(_TWO).promote(_frag(0), CFG)

    def test_agent_unavailable_maps_to_backend_unavailable_category(self):
        class Unavailable(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                raise agent_exec.AgentUnavailableError("agent command not found: claude")

        promoter = llm_promoter.LLMPromoter(
            Unavailable(), skill_text="SKILL", known_projects=["paulshaclaw"]
        )
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            promoter.promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "backend_unavailable")

    def test_agent_timeout_maps_to_transient_category(self):
        class Timeout(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                raise agent_exec.AgentTransientError("agent timed out after 600s")

        promoter = llm_promoter.LLMPromoter(
            Timeout(), skill_text="SKILL", known_projects=["paulshaclaw"]
        )
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            promoter.promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "transient")

    def test_invalid_output_maps_to_invalid_output_category(self):
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            _promoter("garbage not json").promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "invalid_output")

    def test_failed_chunk_clears_previous_raw_output_before_backend_call(self):
        class Sequence(agent_exec.AgentClient):
            def __init__(self) -> None:
                self.calls = 0

            def run(self, prompt: str) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "private echoed prompt"
                raise agent_exec.AgentUnavailableError("backend unavailable")

        promoter = llm_promoter.LLMPromoter(
            Sequence(), skill_text="SKILL", known_projects=["paulshaclaw"]
        )
        with self.assertRaises(llm_promoter.PromoteError):
            promoter._run_chunk("first", [_frag(0)], 1)
        self.assertEqual(promoter.last_raw_output, "private echoed prompt")

        with self.assertRaises(llm_promoter.PromoteError):
            promoter._run_chunk("second", [_frag(0)], 1)

        self.assertEqual(promoter.last_raw_output, "")

    def test_router_session_schema_failure_restarts_all_chunks_and_persists_attempts(self):
        def profile(profile_id: str, tier: int) -> AgentProfile:
            return AgentProfile.from_mapping(
                {
                    "id": profile_id,
                    "tier": tier,
                    "priority": 1,
                    "traits": ["test"],
                    "task_classes": ["atomization"],
                    "model": "test-model",
                    "effort": "medium",
                    "supported_efforts": ["medium"],
                    "argv": [sys.executable, "-c", "pass"],
                }
            )

        valid_0 = (
            '[{"title":"specific finding zero","artifact_kind":"report",'
            '"project":"paulshaclaw","tags":[],"body":"body zero",'
            '"source_fragment_indices":[0],"relations":[]}]'
        )
        valid_1 = (
            '[{"title":"specific finding one","artifact_kind":"report",'
            '"project":"paulshaclaw","tags":[],"body":"body one",'
            '"source_fragment_indices":[1],"relations":[]}]'
        )
        calls: list[tuple[str, str]] = []

        def execute(agent, prompt, attempt):
            calls.append((agent.id, prompt))
            if agent.id == "first" and prompt == "chunk-1":
                return "not schema", "", 0
            return valid_0 if prompt == "chunk-0" else valid_1, "", 0

        parts = []
        for index in (0, 1):
            fragment = _frag(index)
            parts.append(
                llm_promoter.budget.FragmentPart(
                    original_fragment_index=index,
                    part_index=1,
                    part_count=1,
                    body=fragment.body,
                    fragment=fragment,
                )
            )
        chunks = [
            llm_promoter.budget.PromptChunk(
                index=index,
                count=2,
                prompt=f"chunk-{index}",
                estimated_tokens=1,
                parts=(parts[index],),
            )
            for index in (0, 1)
        ]
        router = ExternalAgentRouter(
            (profile("first", 1), profile("second", 2)),
            executor=execute,
        )
        promoter = llm_promoter.LLMPromoter(
            router,
            skill_text="SKILL",
            known_projects=["paulshaclaw"],
        )
        with mock.patch.object(llm_promoter.budget, "pack_prompt_chunks", return_value=chunks):
            slices = promoter.promote([_frag(0), _frag(1)], CFG)

        self.assertEqual(len(slices), 2)
        self.assertEqual(calls, [
            ("first", "chunk-0"),
            ("first", "chunk-1"),
            ("second", "chunk-0"),
            ("second", "chunk-1"),
        ])
        self.assertEqual(promoter.last_provenance["profile_id"], "second")
        self.assertEqual(len(promoter.last_provenance["attempts"]), 2)
        self.assertEqual(promoter.last_provenance["attempts"][0]["failure_category"], "invalid_output")


if __name__ == "__main__":
    unittest.main()
