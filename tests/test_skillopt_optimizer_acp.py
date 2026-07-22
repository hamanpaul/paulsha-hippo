from __future__ import annotations

import unittest
import sys

from paulsha_hippo.agent_profiles import AgentProfile, ExternalAgentRouter
from paulsha_hippo.skillopt import optimizer_acp


class OptimizerRouterTests(unittest.TestCase):
    def test_optimizer_submits_frozen_prompt_to_shared_router(self) -> None:
        seen: list[str] = []

        def execute(profile, prompt, attempt_index):
            del profile, attempt_index
            seen.append(prompt)
            return "---\nname: atomize\n---\nedited skill", "", 0

        profile = AgentProfile.from_mapping(
            {
                "id": "test-router",
                "tier": 1,
                "priority": 1,
                "traits": ["test"],
                "task_classes": ["skillopt"],
                "model": "test-model",
                "effort": "high",
                "supported_efforts": ["high"],
                "argv": [sys.executable, "-V"],
            }
        )
        router = ExternalAgentRouter(
            (profile,),
            task_class="skillopt",
            executor=execute,
        )
        optimizer = optimizer_acp.make_router_optimizer(router)

        output = optimizer(
            "---\nname: atomize\n---\ncurrent skill\n",
            [{"input": "case", "gold": "expected", "output": "got", "score": 0.1}],
        )

        self.assertEqual(output, "---\nname: atomize\n---\nedited skill\n")
        self.assertEqual(len(seen), 1)
        self.assertIn("current skill", seen[0])
        self.assertIn("case", seen[0])
        self.assertEqual(router.last_result.profile_id, "test-router")
        self.assertFalse(hasattr(optimizer_acp, "subprocess"))

    def test_runner_is_an_explicit_injection_seam_only(self) -> None:
        seen: list[str] = []
        optimizer = optimizer_acp.make_router_optimizer(
            runner=lambda prompt: seen.append(prompt) or "---\nname: atomize\n---\nkept"
        )

        self.assertEqual(
            optimizer("---\nname: atomize\n---\ncurrent", []),
            "---\nname: atomize\n---\nkept\n",
        )
        self.assertEqual(len(seen), 1)

    def test_optimizer_requires_one_execution_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            optimizer_acp.make_router_optimizer()


if __name__ == "__main__":
    unittest.main()
