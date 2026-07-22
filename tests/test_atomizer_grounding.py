from __future__ import annotations

import json
from dataclasses import replace

import pytest

from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer.agent_exec import FakeAgentClient
from paulsha_hippo.atomizer.llm_promoter import LLMPromoter, PromoteError
from paulsha_hippo.atomizer.splitter import Fragment


def _fragment(body: str, *, project: str = "paulsha-hippo") -> Fragment:
    return Fragment(
        project=project,
        source_agent="claude-code",
        source_session="semantic-grounding",
        source_artifact="report",
        captured_at="2026-07-22T12:24:00Z",
        provenance={"repo": "r", "commit": "c", "path": "p"},
        fragment_index=0,
        body=body,
    )


def _response(*findings: dict[str, object]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "disposition": "findings",
            "reason": None,
            "findings": list(findings),
        }
    )


def _finding(title: str, body: str, *, project: str = "paulsha-hippo") -> dict[str, object]:
    return {
        "title": title,
        "artifact_kind": "spec",
        "project": project,
        "tags": ["release"],
        "body": body,
        "source_fragment_indices": [0],
        "relations": [],
    }


def test_valid_source_project_is_authoritative_even_when_registry_list_is_stale():
    cfg, _ = atomizer_config.load_config(override_path=None)
    source = _fragment(
        "A release candidate binds one exact commit and wheel hash.",
        project="paulsha-hippo",
    )
    promoter = LLMPromoter(
        FakeAgentClient(
            _response(
                _finding(
                    "Immutable release candidate",
                    "The release candidate binds one exact commit and wheel hash.",
                )
            )
        ),
        skill_text="GROUNDING-SKILL",
        known_projects=["paulshaclaw"],
    )

    slices = promoter.promote([source], cfg)

    assert len(slices) == 1
    assert slices[0].frontmatter["project"] == "paulsha-hippo"


def test_prompt_contract_hallucination_rejects_the_whole_response():
    cfg, _ = atomizer_config.load_config(override_path=None)
    source = _fragment(
        "A release candidate consists of one exact merge commit and one wheel SHA-256. "
        "Build the wheel once and reuse the same bytes for verification and publication."
    )
    promoter = LLMPromoter(
        FakeAgentClient(
            _response(
                _finding(
                    "Immutable release artifact identity",
                    "A release candidate is identified by one merge commit and one wheel SHA-256.",
                ),
                _finding(
                    "Canonical findings JSON contract",
                    "Return one inline JSON object with schema_version, disposition, reason, and a "
                    "findings array. Emit no surrounding prose or Markdown.",
                ),
            )
        ),
        skill_text="GROUNDING-SKILL",
        known_projects=["paulsha-hippo"],
    )

    with pytest.raises(PromoteError, match="not grounded"):
        promoter.promote([source], cfg)


def test_source_owned_output_contract_is_not_rejected_as_prompt_leakage():
    cfg, _ = atomizer_config.load_config(override_path=None)
    source = _fragment(
        "The canonical JSON response uses schema_version, disposition, reason, and a findings "
        "array; Markdown wrappers are forbidden."
    )
    promoter = LLMPromoter(
        FakeAgentClient(
            _response(
                _finding(
                    "Canonical response contract",
                    "The JSON contract contains schema_version, disposition, reason, and findings "
                    "without a Markdown wrapper.",
                )
            )
        ),
        skill_text="GROUNDING-SKILL",
        known_projects=["paulsha-hippo"],
    )

    slices = promoter.promote([source], cfg)

    assert len(slices) == 1


@pytest.mark.parametrize(
    ("source_body", "finding_title", "finding_body"),
    [
        (
            "The release candidate uses an immutable wheel SHA-256 and exact merge commit.",
            "Immutable candidate artifact",
            "Keep the wheel SHA-256 and merge commit unchanged for publication.",
        ),
        (
            "候選版本改變後，所有綁定舊產物的驗證證據都必須失效並重新執行。",
            "候選版本漂移使證據失效",
            "候選版本一旦漂移，舊產物的驗證證據必須失效並重跑。",
        ),
    ],
)
def test_grounded_english_and_cjk_paraphrases_are_accepted(
    source_body: str,
    finding_title: str,
    finding_body: str,
):
    cfg, _ = atomizer_config.load_config(override_path=None)
    promoter = LLMPromoter(
        FakeAgentClient(_response(_finding(finding_title, finding_body))),
        skill_text="GROUNDING-SKILL",
        known_projects=["paulsha-hippo"],
    )

    slices = promoter.promote([_fragment(source_body)], cfg)

    assert len(slices) == 1


def test_grounding_uses_all_bounded_parts_of_a_declared_fragment():
    cfg, _ = atomizer_config.load_config(override_path=None)
    first_part = replace(
        _fragment("The immutable wheel hash identifies the candidate."),
        part_index=1,
        part_count=2,
    )
    second_part = replace(
        _fragment("Publication happens only after all readiness gates pass."),
        part_index=2,
        part_count=2,
    )
    promoter = LLMPromoter(
        FakeAgentClient(
            _response(
                _finding(
                    "Immutable candidate hash",
                    "The immutable wheel hash identifies the release candidate.",
                )
            )
        ),
        skill_text="GROUNDING-SKILL",
        known_projects=["paulsha-hippo"],
    )

    slices = promoter.promote([first_part, second_part], cfg)

    assert len(slices) == 1
