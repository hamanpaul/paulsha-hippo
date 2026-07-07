from __future__ import annotations

import unittest

from paulsha_hippo.atomizer.slice_frontmatter import Slice
from paulsha_hippo.skillopt.scorer import make_hybrid_score, structural_score


def _slice(title: str, body: str, relations: tuple[dict[str, object], ...] = ()) -> Slice:
    return Slice(slice_id="x", frontmatter={}, body=body, title=title, relations=relations)


GOLD = {
    "project": "p",
    "reference_slices": [
        {"title": "WSP_EN sync", "body": "wps_state sync issue", "tags": ["debug"]},
        {"title": "hostapd reload", "body": "vendor adapter reload", "tags": ["debug"]},
    ],
}


class TestStructuralScore(unittest.TestCase):
    def test_score_is_between_0_and_1(self):
        output = [
            _slice("WSP_EN sync", "wps_state sync issue", relations=({"target": "y"},)),
            _slice("hostapd reload", "vendor adapter reload"),
        ]

        score = structural_score(output, GOLD)

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_better_granularity_scores_higher_than_lumped_slice(self):
        good = [
            _slice("WSP_EN sync", "wps_state sync issue"),
            _slice("hostapd reload", "vendor adapter reload"),
        ]
        lumped = [_slice("everything", "wps_state sync issue vendor adapter reload")]

        self.assertGreater(structural_score(good, GOLD), structural_score(lumped, GOLD))

    def test_deterministic_result_for_same_input(self):
        output = [_slice("WSP_EN sync", "wps_state sync issue")]

        self.assertEqual(structural_score(output, GOLD), structural_score(output, GOLD))

    def test_structural_score_does_not_depend_on_reference_slices(self):
        output = [
            _slice("WSP_EN sync", "wps_state sync issue"),
            _slice("hostapd reload", "vendor adapter reload"),
        ]
        unrelated_gold = {
            "project": "p",
            "reference_slices": [
                {"title": "Completely different", "body": "unrelated rubric text", "tags": []},
            ],
        }

        self.assertEqual(structural_score(output, GOLD), structural_score(output, unrelated_gold))


class TestHybridScore(unittest.TestCase):
    def test_make_hybrid_score_uses_alpha_weighting_when_judge_enabled(self):
        class FakeJudge:
            def __init__(self) -> None:
                self.prompt: str | None = None

            def run(self, prompt: str) -> str:
                self.prompt = prompt
                return "0.8"

        output = [
            _slice("WSP_EN sync", "wps_state sync issue"),
            _slice("hostapd reload", "vendor adapter reload"),
        ]
        judge = FakeJudge()
        score = make_hybrid_score(judge, alpha=0.4)
        val_gold = {**GOLD, "judge": {"enabled": True}}
        structural = structural_score(output, val_gold)

        self.assertAlmostEqual(score(output, val_gold), 0.4 * structural + 0.6 * 0.8, places=6)
        self.assertIsNotNone(judge.prompt)
        self.assertIn("atomization quality", judge.prompt)
        self.assertIn("project assignment", judge.prompt)
        self.assertIn("rubric examples only", judge.prompt)
        self.assertIn("Do not treat them as target outputs", judge.prompt)

    def test_make_hybrid_score_rejects_alpha_outside_0_to_1(self):
        class FakeJudge:
            def run(self, prompt: str) -> str:
                del prompt
                return "0.8"

        for alpha in (-0.1, 1.1):
            with self.subTest(alpha=alpha):
                with self.assertRaises(ValueError):
                    make_hybrid_score(FakeJudge(), alpha=alpha)

    def test_judge_output_must_be_an_unambiguous_single_float(self):
        class AmbiguousJudge:
            def run(self, prompt: str) -> str:
                del prompt
                return "0.8 0.6"

        score = make_hybrid_score(AmbiguousJudge(), alpha=0.4)

        with self.assertRaises(ValueError):
            score([_slice("a", "b")], GOLD)

    def test_train_gold_can_disable_judge_for_structural_only_scoring(self):
        class CountingJudge:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, prompt: str) -> str:
                del prompt
                self.calls += 1
                return "0.1"

        output = [
            _slice("WSP_EN sync", "wps_state sync issue"),
            _slice("hostapd reload", "vendor adapter reload"),
        ]
        train_gold = {**GOLD, "judge": {"enabled": False}}
        judge = CountingJudge()
        score = make_hybrid_score(judge, alpha=0.4)

        self.assertAlmostEqual(score(output, train_gold), structural_score(output, train_gold), places=6)
        self.assertEqual(judge.calls, 0)

    def test_judge_exception_propagates(self):
        class BoomJudge:
            def run(self, prompt: str) -> str:
                raise RuntimeError("judge down")

        score = make_hybrid_score(BoomJudge(), alpha=0.4)

        with self.assertRaises(RuntimeError):
            score([_slice("a", "b")], GOLD)


if __name__ == "__main__":
    unittest.main()
