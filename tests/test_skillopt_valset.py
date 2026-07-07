from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.skillopt import valset as skillopt_valset


CFG = AtomizerConfig(
    schema_version="1",
    boundary_patterns=(r"^#{1,6}\s",),
    max_fragment_chars=8000,
    artifact_kind_map={},
    phase_map={},
    default_artifact_kind="report",
    default_phase="review",
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _split_ids(result: dict[str, list[dict[str, object]]]) -> dict[str, list[str]]:
    return {
        "train": [str(item["id"]) for item in result["train"]],
        "val": [str(item["id"]) for item in result["val"]],
    }


class BuildValsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _REPO_ROOT / ".test-work" / "skillopt-valset" / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.inbox_root = self.root / "inbox"
        self.reference_root = self.root / "notes"
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        self.reference_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _write_inbox_doc(
        self,
        *,
        project: str,
        session: str,
        body: str,
        source_agent: str = "claude",
        source_artifact: str = "research",
    ) -> Path:
        path = self.inbox_root / source_artifact / source_agent / "2026-06-04" / f"{session}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    f"project: {project}",
                    f"source_agent: {source_agent}",
                    f"source_session: {session}",
                    f"source_artifact: {source_artifact}",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    f"  path: inbox/{session}.md",
                    "---",
                    body,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _write_reference(self, relative_path: str, text: str) -> Path:
        path = self.reference_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _write_inbox_fragment(
        self,
        *,
        project: str,
        session: str,
        fragment_index: int,
        body: str,
        source_agent: str = "claude",
        source_artifact: str = "research",
    ) -> Path:
        path = self.inbox_root / "_slices" / project / f"{source_agent}__{session}__{fragment_index:03d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    f"project: {project}",
                    f"source_agent: {source_agent}",
                    f"source_session: {session}",
                    f"source_artifact: {source_artifact}",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    f"  path: inbox/_slices/{project}/{source_agent}__{session}__{fragment_index:03d}.md",
                    f"fragment_index: {fragment_index}",
                    f"parent_session_ref: {source_agent}:{session}",
                    "---",
                    body,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _write_raw_inbox_doc(self, relative_path: str, text: str) -> Path:
        path = self.inbox_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _write_raw_inbox_bytes(self, relative_path: str, data: bytes) -> Path:
        path = self.inbox_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _write_reference_bytes(self, relative_path: str, data: bytes) -> Path:
        path = self.reference_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _build(self, **kwargs: object) -> dict[str, list[dict[str, object]]]:
        with mock.patch(
            "paulsha_hippo.skillopt.valset.load_config",
            return_value=(CFG, "cfg-hash"),
        ):
            return skillopt_valset.build_valset(
                inbox_root=self.inbox_root,
                reference_root=self.reference_root,
                **kwargs,
            )

    def test_deterministic_split_on_repeated_runs(self) -> None:
        for index in range(5):
            self._write_inbox_doc(
                project="proj-a",
                session=f"s{index}",
                body=f"# Topic {index}\nbody {index}\n",
            )

        self._write_reference(
            "proj-a/rubric.md",
            "# Reference\nKeep slices focused on one topic.\n",
        )

        first = self._build(val_ratio=0.5, min_project_sample=1)
        second = self._build(val_ratio=0.5, min_project_sample=1)

        self.assertEqual(_split_ids(first), _split_ids(second))
        self.assertEqual(len(first["train"]) + len(first["val"]), 5)

    def test_sparse_project_goes_all_to_train(self) -> None:
        self._write_inbox_doc(
            project="tiny",
            session="sparse-1",
            body="# Only topic\nbody\n",
        )

        result = self._build(val_ratio=1.0, min_project_sample=2)

        self.assertEqual(_split_ids(result), {"train": ["tiny:claude:sparse-1:research#0"], "val": []})

    def test_raw_inbox_doc_yields_fragment_granularity_items(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# First\nalpha\n# Second\nbeta\n",
        )

        result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual([item["input"][0].fragment_index for item in result["train"]], [0, 1])
        self.assertEqual([item["input"][0].body for item in result["train"]], ["# First\nalpha", "# Second\nbeta"])

    def test_persisted_fragments_are_preferred_over_raw_session_and_not_double_counted(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n# Raw second\nraw beta\n",
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=0,
            body="# Persisted first\npersisted alpha\n",
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=1,
            body="# Persisted second\npersisted beta\n",
        )

        result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual(len(result["train"]), 2)
        self.assertEqual(
            [item["input"][0].body for item in result["train"]],
            ["# Persisted first\npersisted alpha", "# Persisted second\npersisted beta"],
        )

    def test_missing_fragment_index_in_persisted_slice_falls_back_to_raw_session(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n# Raw second\nraw beta\n",
        )
        self._write_raw_inbox_doc(
            "_slices/proj-a/claude__s1__000.md",
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    "project: proj-a",
                    "source_agent: claude",
                    "source_session: s1",
                    "source_artifact: research",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    "  path: inbox/_slices/proj-a/claude__s1__000.md",
                    "parent_session_ref: claude:s1",
                    "---",
                    "# Persisted first",
                    "persisted alpha",
                    "",
                ]
            ),
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="WARNING") as logs:
            result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual(
            [item["input"][0].body for item in result["train"]],
            ["# Raw first\nraw alpha", "# Raw second\nraw beta"],
        )
        self.assertTrue(any("invalid fragment metadata" in message and "fragment_index" in message for message in logs.output))

    def test_non_integer_numeric_fragment_index_in_persisted_slice_falls_back_to_raw_session(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n# Raw second\nraw beta\n",
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=0,
            body="# Persisted first\npersisted alpha\n",
        )
        self._write_raw_inbox_doc(
            "_slices/proj-a/claude__s1__001.md",
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    "project: proj-a",
                    "source_agent: claude",
                    "source_session: s1",
                    "source_artifact: research",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    "  path: inbox/_slices/proj-a/claude__s1__001.md",
                    "fragment_index: 1.5",
                    "parent_session_ref: claude:s1",
                    "---",
                    "# Persisted second",
                    "persisted beta",
                    "",
                ]
            ),
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="WARNING") as logs:
            result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual(
            [item["input"][0].body for item in result["train"]],
            ["# Raw first\nraw alpha", "# Raw second\nraw beta"],
        )
        self.assertTrue(any("invalid fragment metadata" in message and "1.5" in message for message in logs.output))

    def test_duplicate_persisted_fragment_index_falls_back_to_raw_session(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n# Raw second\nraw beta\n",
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=0,
            body="# Persisted first\npersisted alpha\n",
        )
        self._write_raw_inbox_doc(
            "_slices/proj-a/claude__s1__000-duplicate.md",
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    "project: proj-a",
                    "source_agent: claude",
                    "source_session: s1",
                    "source_artifact: research",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    "  path: inbox/_slices/proj-a/claude__s1__000-duplicate.md",
                    "fragment_index: 0",
                    "parent_session_ref: claude:s1",
                    "---",
                    "# Persisted first duplicate",
                    "persisted duplicate",
                    "",
                ]
            ),
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=1,
            body="# Persisted second\npersisted beta\n",
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="WARNING") as logs:
            result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual(
            [item["input"][0].body for item in result["train"]],
            ["# Raw first\nraw alpha", "# Raw second\nraw beta"],
        )
        self.assertTrue(any("duplicate persisted fragment_index" in message for message in logs.output))

    def test_partial_persisted_fragment_coverage_falls_back_to_raw_session(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n# Raw second\nraw beta\n",
        )
        self._write_inbox_fragment(
            project="proj-a",
            session="s1",
            fragment_index=0,
            body="# Persisted first\npersisted alpha\n",
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="WARNING") as logs:
            result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {
                "train": [
                    "proj-a:claude:s1:research#0",
                    "proj-a:claude:s1:research#1",
                ],
                "val": [],
            },
        )
        self.assertEqual(
            [item["input"][0].body for item in result["train"]],
            ["# Raw first\nraw alpha", "# Raw second\nraw beta"],
        )
        self.assertTrue(any("incomplete persisted fragment coverage" in message for message in logs.output))

    def test_incomplete_persisted_identity_is_rejected_and_raw_session_fallback_is_kept(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Raw first\nraw alpha\n",
        )
        self._write_raw_inbox_doc(
            "_slices/proj-a/missing-agent.md",
            "\n".join(
                [
                    "---",
                    "memory_layer: inbox",
                    "project: proj-a",
                    "source_session: s1",
                    "source_artifact: research",
                    'captured_at: "2026-06-04T00:00:00Z"',
                    "provenance:",
                    "  repo: hamanpaul/paulshaclaw",
                    "  path: inbox/_slices/proj-a/missing-agent.md",
                    "fragment_index: 0",
                    "parent_session_ref: s1",
                    "---",
                    "# Persisted first",
                    "persisted alpha",
                    "",
                ]
            ),
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="WARNING") as logs:
            result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(
            _split_ids(result),
            {"train": ["proj-a:claude:s1:research#0"], "val": []},
        )
        self.assertEqual([item["input"][0].body for item in result["train"]], ["# Raw first\nraw alpha"])
        self.assertTrue(any("incomplete session identity" in message for message in logs.output))

    def test_same_session_fragments_have_distinct_split_identity(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# First\nalpha\n# Second\nbeta\n",
        )

        seen: list[str] = []

        def fake_hash(value: str) -> float:
            seen.append(value)
            return 0.1 if value.endswith("#0") else 0.9

        with mock.patch("paulsha_hippo.skillopt.valset._hash_fraction", side_effect=fake_hash):
            result = self._build(val_ratio=0.5, min_project_sample=1)

        self.assertEqual(seen, ["proj-a:claude:s1:research#0", "proj-a:claude:s1:research#1"])
        self.assertEqual(_split_ids(result), {"train": ["proj-a:claude:s1:research#1"], "val": ["proj-a:claude:s1:research#0"]})

    def test_missing_reference_domain_gives_empty_reference_slices(self) -> None:
        self._write_inbox_doc(
            project="missing-domain",
            session="m1",
            body="# First\nalpha\n",
        )
        self._write_inbox_doc(
            project="missing-domain",
            session="m2",
            body="# Second\nbeta\n",
        )
        self._write_reference(
            "other-domain/example.md",
            "# Other\nnot for this project\n",
        )

        result = self._build(val_ratio=0.0, min_project_sample=2)

        self.assertEqual(len(result["train"]), 2)
        self.assertEqual(result["val"], [])
        self.assertTrue(all(item["gold"] == {"project": "missing-domain"} for item in result["train"]))

    def test_nested_vault_reference_layout_groups_by_project(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Topic\nbody\n",
        )
        self._write_reference(
            "TechVault/proj-a/ref.md",
            "# Reference\nKeep slices focused on one topic.\n",
        )

        result = self._build(val_ratio=1.0, min_project_sample=1)

        self.assertEqual(result["train"], [])
        self.assertEqual(len(result["val"]), 1)
        self.assertEqual(
            result["val"][0]["gold"]["reference_slices"],
            [{"title": "Reference", "body": "# Reference\nKeep slices focused on one topic.", "tags": []}],
        )

    def test_reference_slices_are_attached_only_to_validation_gold(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# First\nalpha\n",
        )
        self._write_inbox_doc(
            project="proj-a",
            session="s2",
            body="# Second\nbeta\n",
        )
        self._write_reference(
            "proj-a/rubric.md",
            "# Reference\nKeep slices focused on one topic.\n",
        )

        with mock.patch(
            "paulsha_hippo.skillopt.valset._hash_fraction",
            side_effect=lambda value: 0.1 if ":s1:" in value else 0.9,
        ):
            result = self._build(val_ratio=0.5, min_project_sample=1)

        self.assertEqual(len(result["train"]), 1)
        self.assertEqual(len(result["val"]), 1)
        self.assertEqual(result["train"][0]["gold"], {"project": "proj-a"})
        self.assertEqual(
            result["val"][0]["gold"],
            {
                "project": "proj-a",
                "reference_slices": [
                    {
                        "title": "Reference",
                        "body": "# Reference\nKeep slices focused on one topic.",
                        "tags": [],
                    }
                ],
            },
        )

    def test_empty_inbox_yields_empty_train_and_val(self) -> None:
        self.assertEqual(self._build(), {"train": [], "val": []})

    def test_load_inbox_items_disables_machine_local_overrides(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="s1",
            body="# Topic\nbody\n",
        )

        with mock.patch(
            "paulsha_hippo.skillopt.valset.load_config",
            return_value=(CFG, "cfg-hash"),
        ) as load_config_mock:
            items = skillopt_valset.load_inbox_items(self.inbox_root)

        self.assertEqual(len(items), 1)
        load_config_mock.assert_called_once_with(override_path=None)

    def test_malformed_inbox_frontmatter_is_skipped_per_file(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="good",
            body="# Good\nbody\n",
        )
        self._write_raw_inbox_doc(
            "research/claude/2026-06-04/bad.md",
            "---\nproject: [broken\nsource_session: bad\n---\n# Unsafe\nbody\n",
        )

        result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(_split_ids(result), {"train": ["proj-a:claude:good:research#0"], "val": []})

    def test_invalid_utf8_inbox_doc_is_skipped_per_file(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="good",
            body="# Good\nbody\n",
        )
        self._write_raw_inbox_bytes(
            "research/claude/2026-06-04/bad.md",
            b"\xff\xfe# broken\n",
        )

        result = self._build(val_ratio=0.0, min_project_sample=1)

        self.assertEqual(_split_ids(result), {"train": ["proj-a:claude:good:research#0"], "val": []})

    def test_sparse_project_logs_train_downgrade(self) -> None:
        self._write_inbox_doc(
            project="tiny",
            session="sparse-1",
            body="# Only topic\nbody\n",
        )

        with self.assertLogs("paulsha_hippo.skillopt.valset", level="INFO") as logs:
            result = self._build(val_ratio=1.0, min_project_sample=2)

        self.assertEqual(_split_ids(result), {"train": ["tiny:claude:sparse-1:research#0"], "val": []})
        self.assertTrue(
            any("tiny" in message and "train" in message and "min_project_sample=2" in message for message in logs.output)
        )

    def test_malformed_reference_frontmatter_falls_back_to_semantic_body(self) -> None:
        self._write_reference(
            "proj-a/bad-frontmatter.md",
            "---\ntags: [broken\n---\n# Visible\nsemantic body\n",
        )

        references = skillopt_valset.load_reference_slices(self.reference_root)

        self.assertEqual(len(references["proj-a"]), 1)
        self.assertEqual(references["proj-a"][0]["title"], "Visible")
        self.assertEqual(references["proj-a"][0]["body"], "# Visible\nsemantic body")

    def test_invalid_utf8_reference_is_skipped_per_file(self) -> None:
        self._write_reference(
            "proj-a/good.md",
            "# Visible\nsemantic body\n",
        )
        self._write_reference_bytes("proj-a/bad.md", b"\xff\xfe# broken\n")

        references = skillopt_valset.load_reference_slices(self.reference_root)

        self.assertEqual(
            references["proj-a"],
            [{"title": "Visible", "body": "# Visible\nsemantic body", "tags": []}],
        )

    def test_unclosed_frontmatter_is_not_loaded_as_inbox_or_reference(self) -> None:
        self._write_inbox_doc(
            project="proj-a",
            session="good",
            body="# Good\nbody\n",
        )
        self._write_raw_inbox_doc(
            "research/claude/2026-06-04/unclosed.md",
            "---\nproject: proj-a\nsource_session: unclosed\n# Unsafe\nbody\n",
        )
        self._write_reference(
            "proj-a/unclosed.md",
            "---\ntitle: Unsafe\n# Hidden\nbody\n",
        )

        items = skillopt_valset.load_inbox_items(self.inbox_root)
        references = skillopt_valset.load_reference_slices(self.reference_root)

        self.assertEqual([item["id"] for item in items], ["proj-a:claude:good:research#0"])
        self.assertNotIn("unclosed", str(references))
        self.assertEqual(references.get("proj-a", []), [])

    def test_personal_vault_is_excluded_from_reference_loading(self) -> None:
        self._write_reference(
            "PersonalVault/proj-a/hidden.md",
            "# Hidden\nshould be ignored\n",
        )
        self._write_reference(
            "proj-a/visible.md",
            "---\ntags:\n- keep\n---\n# Visible\nsemantic body\n",
        )

        references = skillopt_valset.load_reference_slices(self.reference_root)

        self.assertNotIn("PersonalVault", references)
        self.assertEqual(len(references["proj-a"]), 1)
        self.assertEqual(references["proj-a"][0]["title"], "Visible")
        self.assertEqual(references["proj-a"][0]["body"], "# Visible\nsemantic body")


if __name__ == "__main__":
    unittest.main()
