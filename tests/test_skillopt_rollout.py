from __future__ import annotations

import unittest
from unittest import mock

from paulsha_hippo.atomizer import prompt as atomizer_prompt
from paulsha_hippo.atomizer.agent_exec import FakeAgentClient
from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.atomizer.splitter import Fragment
from paulsha_hippo.skillopt.rollout import make_atomize_rollout


CFG = AtomizerConfig(
    schema_version="1",
    boundary_patterns=(r"^#{1,6}\s",),
    max_fragment_chars=8000,
    artifact_kind_map={},
    phase_map={},
    default_artifact_kind="report",
    default_phase="review",
)

_OUTPUT = (
    '[{"title":"slice title","artifact_kind":"report","project":"paulshaclaw",'
    '"tags":["skillopt"],"body":"slice body","source_fragment_indices":[0],"relations":[]}]'
)


class RecordingFakeAgentClient(FakeAgentClient):
    def __init__(self, canned_output: str) -> None:
        super().__init__(canned_output)
        self.last_prompt: str | None = None

    def run(self, prompt: str) -> str:
        self.last_prompt = prompt
        return super().run(prompt)


def _fragment(index: int = 0) -> Fragment:
    return Fragment(
        project="paulshaclaw",
        source_agent="claude",
        source_session="s1",
        source_artifact="research",
        captured_at="2026-06-04T00:00:00Z",
        provenance={"repo": "hamanpaul/paulshaclaw"},
        fragment_index=index,
        body="some session body",
    )


class AtomizeRolloutTests(unittest.TestCase):
    def test_candidate_skill_text_is_injected_into_prompt(self):
        candidate = "---\nname: atomize\n---\nCANDIDATE-MARKER principles\n"
        fragments = [_fragment(0)]
        agent = RecordingFakeAgentClient(_OUTPUT)

        rollout = make_atomize_rollout(
            agent,
            known_projects=["paulshaclaw"],
            config=CFG,
        )

        output = rollout(candidate, fragments)

        self.assertEqual(
            agent.last_prompt,
            atomizer_prompt.build_prompt(candidate, fragments, ["paulshaclaw"]),
        )
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].title, "slice title")
        self.assertEqual(output[0].body, "slice body")
        self.assertEqual(output[0].frontmatter["project"], "paulshaclaw")
        self.assertEqual(output[0].frontmatter["source_fragments"], [0])

    def test_empty_fragments_returns_empty(self):
        rollout = make_atomize_rollout(
            RecordingFakeAgentClient(_OUTPUT),
            known_projects=["paulshaclaw"],
            config=CFG,
        )

        self.assertEqual(rollout("---\nname: atomize\n---\nbody\n", []), [])

    def test_missing_config_uses_default_override_resolution(self):
        default_cfg = AtomizerConfig(
            schema_version="1",
            boundary_patterns=(r"^#{1,6}\s",),
            max_fragment_chars=8000,
            artifact_kind_map={},
            phase_map={},
            default_artifact_kind="report",
            default_phase="review",
            agent_exec_model="model-from-default-override",
        )
        disabled_cfg = AtomizerConfig(
            schema_version="1",
            boundary_patterns=(r"^#{1,6}\s",),
            max_fragment_chars=8000,
            artifact_kind_map={},
            phase_map={},
            default_artifact_kind="report",
            default_phase="review",
            agent_exec_model="model-with-overrides-disabled",
        )
        promoted: dict[str, object] = {}

        def fake_load_config(*args, **kwargs):
            if kwargs.get("override_path", mock.sentinel.unset) is None:
                return disabled_cfg, "disabled"
            return default_cfg, "default"

        class FakePromoter:
            def __init__(self, agent_client, skill_text, projects, *, model):
                promoted["model"] = model

            def promote(self, fragments, cfg):
                promoted["cfg"] = cfg
                return []

        with (
            mock.patch("paulsha_hippo.skillopt.rollout.load_config", side_effect=fake_load_config) as load_config_mock,
            mock.patch("paulsha_hippo.skillopt.rollout.LLMPromoter", FakePromoter),
        ):
            rollout = make_atomize_rollout(
                RecordingFakeAgentClient(_OUTPUT),
                known_projects=["paulshaclaw"],
            )
            self.assertEqual(rollout("---\nname: atomize\n---\nbody\n", [_fragment(0)]), [])

        load_config_mock.assert_called_once_with()
        self.assertIs(promoted["cfg"], default_cfg)
        self.assertEqual(promoted["model"], "model-from-default-override")


if __name__ == "__main__":
    unittest.main()
