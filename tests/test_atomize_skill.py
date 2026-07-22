from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "atomizer" / "skills" / "atomize-knowledge-slice.md"


class SkillDocTests(unittest.TestCase):
    maxDiff = None

    def _read_skill(self) -> tuple[dict[str, object], str]:
        self.assertTrue(SKILL.exists(), f"missing skill doc: {SKILL}")
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "skill doc must start with frontmatter")
        self.assertIn("\n---\n", text[4:], "skill doc must close frontmatter")
        frontmatter, body = text[4:].split("\n---\n", maxsplit=1)

        payload: dict[str, object] = {}
        current_key: str | None = None
        for line in frontmatter.splitlines():
            if line.startswith("  - "):
                self.assertIsNotNone(current_key, "frontmatter list item must follow a key")
                payload.setdefault(current_key, [])
                assert isinstance(payload[current_key], list)
                payload[current_key].append(line[4:])
                continue

            key, separator, value = line.partition(":")
            self.assertTrue(separator, f"invalid frontmatter line: {line}")
            current_key = key.strip()
            value = value.strip()
            payload[current_key] = value.strip('"') if value else []

        return payload, body

    def _extract_phase(self, body: str, phase_name: str) -> str:
        match = re.search(
            rf"^### \d+\. {re.escape(phase_name)}\n(.*?)(?=^### \d+\. |^## |\Z)",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"missing phase section: {phase_name}")
        return match.group(1)

    def test_skill_frontmatter_has_required_keys(self):
        frontmatter, _ = self._read_skill()

        self.assertEqual(frontmatter["name"], "atomize-knowledge-slice")
        self.assertTrue(frontmatter["description"])
        self.assertEqual(
            frontmatter["triggers"],
            ["atomize knowledge slice", "llm atomizer", "語意原子化"],
        )

    def test_skill_locks_workflow_phases_and_distillation_rules(self):
        _, body = self._read_skill()

        self.assertIn("## 六階段工作流", body)
        self.assertEqual(
            re.findall(r"^### \d+\. ([A-Z_]+)$", body, flags=re.MULTILINE),
            [
                "SESSION_SCAN",
                "CONCEPT_ANALYSIS",
                "SLICE_PLANNING",
                "DRAFT_SLICES",
                "PROJECT_TAG_RELATION_PASS",
                "VALIDATE",
            ],
        )
        self.assertIn("不複製原筆記內容", body)
        self.assertIn("不要照抄 fragments；改寫成精簡、可單獨閱讀的知識切片。", body)

    def test_skill_locks_project_tag_relation_guidance(self):
        _, body = self._read_skill()
        phase = self._extract_phase(body, "PROJECT_TAG_RELATION_PASS")

        self.assertIn("每個 slice 必須從提供的 known projects 中選一個最貼切的 `project`。", phase)
        self.assertIn(
            "若根本沒有提供 known projects 清單，或沒有可信歸屬、內容跨多專案且無單一主軸時，才用 `_unknown`。",
            phase,
        )
        self.assertIn("`tags` 先放全域 tag，再放概念 tag。", phase)
        self.assertIn("tag 應偏向檢索鍵，而不是句子或過長描述。", phase)
        self.assertIn("`relates_to` 只能指向同一批輸出的另一個 slice，且 `target_title` 必須精確等於對方 `title`。", phase)
        self.assertIn("`mentions` 使用 `{ \"type\": \"mentions\", \"entity\": \"NAME\" }`；entity 用穩定名稱，不加多餘敘述。", phase)

    def test_skill_output_contract_locks_fields_unknown_fallback_and_relation_variants(self):
        _, body = self._read_skill()

        self.assertIn("## Output contract", body)
        _, output_contract = body.split("## Output contract", maxsplit=1)
        self.assertIn("Return ONLY a canonical JSON object.", output_contract)
        self.assertIn('"schema_version":1', output_contract)
        self.assertIn('"disposition":"no_findings"', output_contract)
        self.assertIn('"reason":"no durable findings"', output_contract)
        self.assertIn('"findings":[]', output_contract)
        for field in (
            "title",
            "artifact_kind",
            "project",
            "tags",
            "body",
            "source_fragment_indices",
            "relations",
        ):
            self.assertIn(field, output_contract)
        for artifact_kind in (
            "research",
            "spec",
            "roadmap",
            "test",
            "task",
            "todo",
            "plan",
            "report",
            "review",
            "ship-record",
            "gate-report",
        ):
            self.assertIn(artifact_kind, output_contract)
        self.assertIn("若未提供清單或無法可靠歸屬才用 `_unknown`。", output_contract)
        self.assertIn("只允許下列兩種物件：", output_contract)
        self.assertEqual(
            [line.strip() for line in output_contract.splitlines() if line.strip().startswith('- `{')],
            [
                '- `{ "type": "relates_to", "target_title": "<another slice title>" }`',
                '- `{ "type": "mentions", "entity": "<stable entity name>" }`',
            ],
        )

    def test_skill_output_contract_forbids_file_writes_and_prose(self):
        _, body = self._read_skill()

        _, output_contract = body.split("## Output contract", maxsplit=1)
        self.assertIn("Return ONLY a canonical JSON object.", output_contract)
        self.assertIn("The first character of your response must be `{` and the last character must be `}`.", output_contract)
        self.assertIn("Do NOT create files, write files, save files, or claim that you updated any file or index.", output_contract)
        self.assertIn("Do NOT return prose, narration, summaries, markdown fences, or any text before or after the JSON object.", output_contract)
        self.assertNotIn("```", output_contract)


if __name__ == "__main__":
    unittest.main()
