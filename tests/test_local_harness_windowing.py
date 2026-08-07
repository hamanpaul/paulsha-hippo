"""issue #74：local-harness pass 2 的 content-addressed input windowing。

`contrib/local-harness/harness.py` 是本機部署產物（不進 wheel），但它是
tier-3 保底層的實作，切輸入的正確性直接決定大 session 會不會 park，故在
repo 測試套件內守護。以 importlib 由檔案路徑載入（目錄含連字號、非套件），
模組本身無 import 副作用（main() 有 __main__ 保護）。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from paulsha_hippo.atomizer.prompt import build_prompt
from paulsha_hippo.atomizer.splitter import Fragment

HARNESS_PATH = Path(__file__).resolve().parents[1] / "contrib" / "local-harness" / "harness.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("hippo_local_harness", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _fragment(index: int, body: str, *, part_index: int = 1, part_count: int = 1) -> Fragment:
    return Fragment(
        project="proj",
        source_agent="claude",
        source_session="sess",
        source_artifact="artifact.md",
        captured_at="2026-08-01T00:00:00Z",
        provenance={},
        fragment_index=index,
        body=body,
        part_index=part_index,
        part_count=part_count,
    )


def _prompt(fragments: list[Fragment]) -> str:
    return build_prompt("SKILL CONTRACT TEXT", fragments, ["proj"])


class WindowPromptTests(unittest.TestCase):
    def test_keeps_only_requested_fragments_and_their_neighbours(self):
        prompt = _prompt([_fragment(i, f"body-of-{i}") for i in range(10)])

        windowed = harness.window_prompt(prompt, [5], neighbours=1)

        for kept in (4, 5, 6):
            self.assertIn(f"body-of-{kept}", windowed)
        for dropped in (0, 1, 2, 3, 7, 8, 9):
            self.assertNotIn(f"body-of-{dropped}", windowed)

    def test_preserves_preamble_and_output_contract(self):
        # WRITE_INSTRUCTION 明說「follow the field rules from the skill contract
        # in the original prompt」，切掉 preamble 會讓 pass 2 失去欄位契約。
        prompt = _prompt([_fragment(i, f"body-of-{i}") for i in range(10)])

        windowed = harness.window_prompt(prompt, [5], neighbours=0)

        self.assertIn("SKILL CONTRACT TEXT", windowed)
        self.assertIn("## Known projects", windowed)
        self.assertIn("## Session fragments to atomize", windowed)
        self.assertIn("## Output", windowed)
        self.assertIn("Return ONLY a canonical JSON object.", windowed)

    def test_keeps_every_part_of_a_split_fragment(self):
        prompt = _prompt([
            _fragment(0, "body-of-0"),
            _fragment(1, "first-half", part_index=1, part_count=2),
            _fragment(1, "second-half", part_index=2, part_count=2),
            _fragment(2, "body-of-2"),
        ])

        windowed = harness.window_prompt(prompt, [1], neighbours=0)

        self.assertIn("first-half", windowed)
        self.assertIn("second-half", windowed)
        self.assertNotIn("body-of-0", windowed)
        self.assertNotIn("body-of-2", windowed)

    def test_substantially_shrinks_a_large_session(self):
        # #74 的核心量測：48 fragments / ~100KB，每個 concept 只需少數 fragment。
        fragments = [_fragment(i, f"body-of-{i} " + "x" * 2000) for i in range(48)]
        prompt = _prompt(fragments)

        windowed = harness.window_prompt(prompt, [10, 11, 12], neighbours=1)

        self.assertLess(len(windowed), len(prompt) / 4)

    def test_falls_back_to_full_prompt_when_structure_is_unrecognised(self):
        # prompt 契約若改變，切輸入必須整個放棄而不是送出殘缺 prompt。
        prompt = "no headers here, just prose"
        self.assertEqual(harness.window_prompt(prompt, [0]), prompt)

    def test_falls_back_to_full_prompt_when_no_fragment_matches(self):
        # 越界／錯誤的 fragment_indices 不得產生零 fragment 的 prompt。
        prompt = _prompt([_fragment(i, f"body-of-{i}") for i in range(3)])
        self.assertEqual(harness.window_prompt(prompt, [99], neighbours=0), prompt)

    def test_falls_back_to_full_prompt_when_indices_are_absent(self):
        prompt = _prompt([_fragment(i, f"body-of-{i}") for i in range(3)])
        self.assertEqual(harness.window_prompt(prompt, []), prompt)


class BuildWriteMessageTests(unittest.TestCase):
    """pass 2 必須真的用切過的 prompt——守護「有沒有接上」，而非只守護切法。"""

    def test_write_message_carries_only_the_concepts_fragments(self):
        # fragment 需有實際體積，否則 WRITE_INSTRUCTION 的固定開銷會蓋過
        # 切輸入的效果，讓大小斷言失去意義。
        prompt = _prompt([
            _fragment(i, f"body-of-{i} " + "x" * 2000) for i in range(10)
        ])
        concept = {
            "title": "the concept",
            "artifact_kind": "report",
            "fragment_indices": [5],
        }

        message = harness.build_write_message(prompt, concept, ["other"])

        self.assertIn("the concept", message)       # 指令段
        self.assertIn("body-of-5", message)         # 命中的 fragment
        self.assertNotIn("body-of-0", message)      # 未命中的 fragment 不得重送
        self.assertNotIn("body-of-9", message)
        self.assertLess(len(message), len(prompt))

    def test_write_message_still_contains_the_skill_contract(self):
        prompt = _prompt([_fragment(i, f"body-of-{i}") for i in range(10)])
        concept = {
            "title": "the concept",
            "artifact_kind": "report",
            "fragment_indices": [5],
        }

        message = harness.build_write_message(prompt, concept, ["other"])

        self.assertIn("SKILL CONTRACT TEXT", message)


if __name__ == "__main__":
    unittest.main()
