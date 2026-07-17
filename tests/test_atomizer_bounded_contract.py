from __future__ import annotations

import json

import pytest

from paulsha_hippo.atomizer import budget, llm_output
from paulsha_hippo.atomizer.config import (
    MIN_CONTEXT_WINDOW as CONFIG_MIN_CONTEXT_WINDOW,
    AtomizerConfig,
    DEFAULT_AGENT_EXEC_COMMAND,
)
from paulsha_hippo.atomizer.splitter import Fragment


def _fragment(index: int, body: str) -> Fragment:
    return Fragment(
        project="paulsha-hippo",
        source_agent="codex",
        source_session="large-session",
        source_artifact="session",
        captured_at="2026-07-16T00:00:00Z",
        provenance={"repo": "r", "commit": "c", "path": "p"},
        fragment_index=index,
        body=body,
    )


def _finding(title: str = "bounded atomization") -> dict[str, object]:
    return {
        "title": title,
        "artifact_kind": "report",
        "project": "paulsha-hippo",
        "tags": ["atomizer"],
        "body": "preserved finding",
        "source_fragment_indices": [0],
        "relations": [],
    }


def test_canonical_response_distinguishes_findings_and_no_findings():
    findings = llm_output.parse_response(
        json.dumps(
            {
                "schema_version": 1,
                "disposition": "findings",
                "reason": None,
                "findings": [_finding()],
            }
        ),
        ["paulsha-hippo"],
    )
    no_findings = llm_output.parse_response(
        json.dumps(
            {
                "schema_version": 1,
                "disposition": "no_findings",
                "reason": "session only contains acknowledgements",
                "findings": [],
            }
        ),
        ["paulsha-hippo"],
    )

    assert findings.disposition == "findings"
    assert len(findings.findings) == 1
    assert no_findings.disposition == "no_findings"
    assert no_findings.reason == "session only contains acknowledgements"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "",
        '{"findings":[]}',
        '{"schema_version":1,"disposition":"no_findings","reason":"","findings":[]}',
        '{"schema_version":1,"disposition":"findings","reason":null,"findings":[],"extra":1}',
        'noise {"schema_version":1,"disposition":"no_findings","reason":"none","findings":[]}',
    ],
)
def test_empty_noise_and_invalid_wrappers_are_rejected(raw):
    with pytest.raises(llm_output.LlmOutputError):
        llm_output.parse_response(raw, ["paulsha-hippo"])


def test_non_empty_legacy_array_is_temporarily_accepted():
    response = llm_output.parse_response(json.dumps([_finding()]), ["paulsha-hippo"])
    assert response.legacy is True
    assert response.disposition == "findings"


def test_provider_context_boundary():
    assert budget.CONTEXT_WINDOW == budget.MIN_CONTEXT_WINDOW
    assert CONFIG_MIN_CONTEXT_WINDOW == budget.MIN_CONTEXT_WINDOW
    assert budget.provider_context_supported(32767) is False
    assert budget.provider_context_supported(32768) is True
    assert budget.provider_context_supported(32769) is True
    assert budget.provider_context_supported(262144) is True


def test_pack_prompt_chunks_rejects_provider_context_below_minimum():
    with pytest.raises(
        budget.ContextBudgetExceeded,
        match=r"provider context 32767 < minimum 32768",
    ):
        budget.pack_prompt_chunks(
            skill_text="skill contract",
            fragments=[_fragment(0, "body")],
            known_projects=["paulsha-hippo"],
            context_window=32767,
        )


def test_large_fragments_are_fully_covered_by_ordered_bounded_chunks():
    paragraphs = [f"paragraph-{index:04d} " + ("x" * 180) for index in range(1200)]
    source = "\n\n".join(paragraphs)
    chunks = budget.pack_prompt_chunks(
        skill_text="skill contract",
        fragments=[_fragment(0, source)],
        known_projects=["paulsha-hippo"],
    )

    assert len(chunks) > 1
    assert [chunk.index for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(chunk.count == len(chunks) for chunk in chunks)
    assert all(chunk.estimated_tokens <= budget.SAFE_INPUT_TOKENS for chunk in chunks)
    assert all(len(chunk.prompt.encode("utf-8")) <= budget.MAX_PROMPT_ARGV_BYTES for chunk in chunks)
    assert [part.body for chunk in chunks for part in chunk.parts]
    reconstructed = "".join(
        part.body for chunk in chunks for part in chunk.parts if part.original_fragment_index == 0
    )
    assert reconstructed == source
    assert all("part " in chunk.prompt for chunk in chunks)


def test_larger_provider_context_does_not_widen_prompt_chunk_gates():
    source = "\n\n".join(
        f"paragraph-{index:04d} " + ("x" * 180) for index in range(1200)
    )
    kwargs = {
        "skill_text": "skill contract",
        "fragments": [_fragment(0, source)],
        "known_projects": ["paulsha-hippo"],
    }

    chunks_32k = budget.pack_prompt_chunks(**kwargs, context_window=32768)
    chunks_256k = budget.pack_prompt_chunks(**kwargs, context_window=262144)

    assert chunks_256k == chunks_32k
    assert all(chunk.estimated_tokens <= budget.SAFE_INPUT_TOKENS for chunk in chunks_256k)
    assert all(
        len(chunk.prompt.encode("utf-8")) <= budget.MAX_PROMPT_ARGV_BYTES
        for chunk in chunks_256k
    )
    reconstructed = "".join(
        part.body for chunk in chunks_256k for part in chunk.parts
    )
    assert reconstructed == source


def test_oversized_fragment_preserves_all_whitespace_byte_for_byte():
    source = ("leading\n\n\n\nparagraph\n\n" + ("x" * 60000) + "\n\n\ntrailing\n")

    chunks = budget.pack_prompt_chunks(
        skill_text="skill contract",
        fragments=[_fragment(0, source)],
        known_projects=["paulsha-hippo"],
    )

    reconstructed = "".join(
        part.body for chunk in chunks for part in chunk.parts if part.original_fragment_index == 0
    )
    assert reconstructed == source


def test_atomizer_bounded_defaults_and_fixed_safety_gates():
    cfg = AtomizerConfig(
        schema_version="1",
        boundary_patterns=(),
        max_fragment_chars=8000,
        artifact_kind_map={},
        phase_map={},
    )
    assert cfg.context_window == 32768
    assert cfg.max_input_tokens == 12000
    assert cfg.agent_exec_max_output_tokens == 2048
    assert cfg.max_prompt_argv_bytes == 48 * 1024
    assert cfg.agent_exec_timeout == 300
    assert cfg.chunk_retries == 2
    assert cfg.parallelism == 1


def test_default_gemma_argv_is_explicitly_zero_tool_without_fallback_profile():
    command = " ".join(DEFAULT_AGENT_EXEC_COMMAND)
    for flag in (
        "--available-tools=none",
        "--disable-builtin-mcps",
        "--no-custom-instructions",
        "--no-ask-user",
        "--no-remote",
        "--no-remote-export",
    ):
        assert flag in command
    assert "--available-tools=" in command
    assert "--available-tools=none" in command
