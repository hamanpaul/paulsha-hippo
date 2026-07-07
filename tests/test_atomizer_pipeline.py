from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import agent_exec
from paulsha_hippo.atomizer import llm_promoter, pipeline, slice_frontmatter
from paulsha_hippo.atomizer.agent_exec import FakeAgentClient
from paulsha_hippo.atomizer.promoter import Promoter
from paulsha_hippo.atomizer.splitter import Fragment
from paulsha_hippo.ledger import processing, relations
from paulsha_hippo.moc import frontmatter_io as fio

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-05-31T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha body
# Topic B
beta body
"""


def _seed_raw(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW, encoding="utf-8")
    return raw


_RAW_S2 = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s2
source_artifact: research
captured_at: "2026-05-31T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/y.md
---
# Topic C
gamma body
# Topic D
delta body
"""


def _seed_raw_s2(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s2.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW_S2, encoding="utf-8")
    return raw


class FailingPromoter(Promoter):
    """Promoter that makes fragment_index==1 of s1 fail validation."""

    def __init__(self, fail_session: str = "s1", fail_index: int = 1):
        self.fail_session = fail_session
        self.fail_index = fail_index

    def promote(
        self, fragments: list[Fragment], config: atomizer_config.AtomizerConfig
    ) -> list[slice_frontmatter.Slice]:
        promoted = []
        for fragment in fragments:
            slice_ = slice_frontmatter.build(fragment, config)
            if (
                fragment.source_session == self.fail_session
                and fragment.fragment_index == self.fail_index
            ):
                bad_fm = dict(slice_.frontmatter)
                del bad_fm["memory_layer"]  # This will fail T4 validation
                slice_ = slice_frontmatter.Slice(
                    slice_id=slice_.slice_id,
                    frontmatter=bad_fm,
                    body=slice_.body,
                )
            promoted.append(slice_)
        return promoted


class BrokenReferencePromoter(Promoter):
    """Promoter that returns one valid slice and one slice without source refs."""

    def promote(
        self, fragments: list[Fragment], config: atomizer_config.AtomizerConfig
    ) -> list[slice_frontmatter.Slice]:
        good = slice_frontmatter.build(fragments[0], config)
        bad = slice_frontmatter.build(fragments[1], config)
        bad_frontmatter = dict(bad.frontmatter)
        bad_frontmatter.pop("fragment_ref", None)
        return [
            good,
            slice_frontmatter.Slice(
                slice_id=bad.slice_id,
                frontmatter=bad_frontmatter,
                body=bad.body,
            ),
        ]


class UnsupportedRelationPromoter(Promoter):
    """Promoter that injects an unsupported semantic relation."""

    def promote(
        self, fragments: list[Fragment], config: atomizer_config.AtomizerConfig
    ) -> list[slice_frontmatter.Slice]:
        base = slice_frontmatter.build(fragments[0], config)
        return [
            slice_frontmatter.Slice(
                slice_id=base.slice_id,
                frontmatter=dict(base.frontmatter),
                body=base.body,
                relations=({"type": "supersedes", "target_title": "x"},),
            )
        ]


class ExplodingPromoter(Promoter):
    """Promoter that raises an unexpected exception for one session."""

    def __init__(self, fail_session: str = "s1") -> None:
        self.fail_session = fail_session

    def promote(
        self, fragments: list[Fragment], config: atomizer_config.AtomizerConfig
    ) -> list[slice_frontmatter.Slice]:
        if fragments and fragments[0].source_session == self.fail_session:
            raise RuntimeError("boom")
        return [slice_frontmatter.build(fragment, config) for fragment in fragments]


class ScriptedAgentClient(agent_exec.AgentClient):
    """Return scripted outputs or raise scripted exceptions in order."""

    def __init__(self, outputs: list[str | Exception]) -> None:
        self._outputs = list(outputs)
        self.calls = 0

    def run(self, prompt: str) -> str:
        del prompt
        output = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        if isinstance(output, Exception):
            raise output
        return output


_VALID_ONE_SLICE = (
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],'
    '"body":"body a","source_fragment_indices":[0,1],"relations":[]}]'
)


class PipelineTests(unittest.TestCase):
    _RAW_DOC_FRAGMENT = (
        "---\nmemory_layer: inbox\nproject: paulshaclaw\nsource_agent: claude\n"
        "source_session: sdoc\nsource_artifact: research\ncaptured_at: \"2026-05-31T00:00:00Z\"\n"
        "provenance:\n  repo: paulshaclaw\n  commit: c\n  path: AGENTS.md\n---\n"
        "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開\n"
    )

    def _seed_doc_fragment(self, root: Path) -> None:
        raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "sdoc.md"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(self._RAW_DOC_FRAGMENT, encoding="utf-8")

    def test_doc_fragment_dropped_at_produce_time_with_corpus(self):
        from paulsha_hippo.noise import build_corpus
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_doc_fragment(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            corpus = build_corpus([
                "## 動工前\n- [ ] 確認當前分支不是 `main`\n- [ ] 跨多子項先用 `git worktree` 拆開\n"])
            result = pipeline.run(root, config=cfg, config_hash=h,
                                  now="2026-05-31T03:00:00Z", doc_corpus=corpus)
            self.assertEqual(result["summary"]["noise_dropped"], 1)
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            # source fragment still archived (session promoted, not stuck)
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])

    def test_doc_fragment_kept_without_corpus(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_doc_fragment(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            self.assertEqual(result["summary"]["noise_dropped"], 0)
            self.assertEqual(len(list((root / "knowledge").rglob("*.md"))), 1)

    def test_split_pass_creates_fragments_and_archives_raw(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            self.assertFalse(raw.exists())  # raw archived out of raw layer
            self.assertTrue(list((root / "archive" / "sessions").rglob("*.md")))
            self.assertTrue(list((root / "knowledge").rglob("*.md")))
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")

    def test_one_to_one_slice_count_matches_fragments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            self.assertEqual(result["summary"]["slices"], 2)  # two headings -> two slices

    def test_idempotent_second_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            kwargs = dict(config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            pipeline.run(root, **kwargs)
            before = len(list((root / "knowledge").rglob("*.md")))
            result2 = pipeline.run(root, **kwargs)
            self.assertEqual(result2["summary"]["slices"], 0)
            self.assertEqual(len(list((root / "knowledge").rglob("*.md"))), before)

    def test_flow_through_empties_working_layers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertTrue(list((root / "archive" / "fragments").rglob("*.md")))

    def test_identity_pipeline_uses_fragment_ref_fallback_and_records_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)

            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")

            self.assertEqual(result["summary"]["slices"], 2)
            edges = relations.read_edges(root)
            promoted = [edge for edge in edges if edge["type"] == "promoted_to"]
            self.assertEqual(
                {edge["from"] for edge in promoted},
                {
                    "fragment:claude__s1__000",
                    "fragment:claude__s1__001",
                },
            )
            self.assertEqual(len({edge["to"] for edge in promoted}), 2)
            promoted_event = processing.read_events(root)[-1]
            self.assertEqual(promoted_event["promoter"], "identity")

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", dry_run=True)
            self.assertTrue(raw.exists())
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            self.assertGreater(result["summary"]["slices"], 0)

    def test_dry_run_llm_merge_uses_per_session_promoter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"merged","artifact_kind":"report","project":"paulshaclaw","tags":["x"],'
                    '"body":"merged body","source_fragment_indices":[0,1],"relations":[]}]'
                ),
                skill_text="MERGE-SKILL",
                known_projects=["paulshaclaw"],
            )

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:00:00Z",
                dry_run=True,
                promoter=promoter,
            )

            self.assertTrue(raw.exists())
            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])

    def test_traversal_project_is_sanitized_without_writing_outside_root(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "memory"
            escaped = parent / "escaped"
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(_RAW.replace("project: paulshaclaw", "project: ../../../escaped"),
                           encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")

            # Security: a traversal-style project MUST NOT write anything outside the root.
            self.assertFalse(escaped.exists())
            root_prefix = str(root.resolve())
            for path in parent.rglob("*"):
                if path.is_file():
                    self.assertTrue(
                        str(path.resolve()).startswith(root_prefix),
                        msg=f"wrote outside memory root: {path}",
                    )
            # New behavior: project is sanitized (not skipped); the session is processed.
            self.assertGreaterEqual(result["summary"]["split_sessions"], 1)
            self.assertFalse(any("unsafe path field" in w for w in result["warnings"]))

    def test_oversize_raw_inbox_file_is_skipped_and_recorded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = _seed_raw(root)
            raw.write_text(_RAW + ("X" * 200), encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)

            with (
                mock.patch.object(pipeline, "_ATOMIZER_INBOX_FILE_MAX_BYTES", 64, create=True),
                self.assertLogs("paulsha_hippo.atomizer.pipeline", level="WARNING") as captured,
            ):
                result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")

            self.assertTrue(raw.exists())
            self.assertEqual(processing.state_of(root, "claude:s1"), "skipped")
            self.assertGreater(result["summary"]["skipped"], 0)
            self.assertTrue(any("exceeds 64 bytes" in warning for warning in result["warnings"]))
            latest_event = processing.read_events(root)[-1]
            self.assertEqual(latest_event["state"], "skipped")
            self.assertIn("file too large", latest_event["skip_reason"])
            self.assertIn("exceeds 64 bytes", "\n".join(captured.output))

    def test_oversize_raw_inbox_file_is_silently_skipped_after_first_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = _seed_raw(root)
            raw.write_text(_RAW + ("X" * 200), encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)

            with mock.patch.object(
                pipeline,
                "_ATOMIZER_INBOX_FILE_MAX_BYTES",
                64,
                create=True,
            ):
                pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
                first_events = processing.read_events(root)

                with self.assertNoLogs(
                    "paulsha_hippo.atomizer.pipeline",
                    level="WARNING",
                ):
                    second = pipeline.run(
                        root,
                        config=cfg,
                        config_hash=h,
                        now="2026-05-31T04:00:00Z",
                    )

            second_events = processing.read_events(root)
            self.assertEqual(len(second_events), len(first_events))
            self.assertEqual(processing.state_of(root, "claude:s1"), "skipped")
            self.assertEqual(second["warnings"], [])
            self.assertEqual(second["summary"]["skipped"], 0)

    def test_llm_merge_path_writes_one_slice_and_semantic_edges(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            skill_text = "MERGE-SKILL"
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"merged","artifact_kind":"report","project":"paulshaclaw","tags":["alpha"],'
                    '"body":"merged body","source_fragment_indices":[0,1],'
                    '"relations":[{"type":"mentions","entity":"MTK"}]}]'
                ),
                skill_text=skill_text,
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            written = list((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertEqual(len(written), 1)

            edges = relations.read_edges(root)
            promoted = [e for e in edges if e["type"] == "promoted_to"]
            mentions = [e for e in edges if e["type"] == "mentions"]
            self.assertEqual(len(promoted), 2)
            self.assertEqual(len(mentions), 1)
            self.assertEqual(mentions[0]["to"], "entity:MTK")
            self.assertEqual({e["from"] for e in promoted}, {"fragment:claude__s1__000", "fragment:claude__s1__001"})
            promoted_event = processing.read_events(root)[-1]
            self.assertEqual(promoted_event["promoter"], "llm")
            self.assertEqual(promoted_event["model"], "fake-llm")
            self.assertEqual(
                promoted_event["skill_hash"],
                hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
            )

    def test_llm_empty_output_reaches_promoted_terminal_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cached_client = agent_exec.CachingAgentClient(FakeAgentClient("[]"), cache_dir)
            promoter = llm_promoter.LLMPromoter(
                cached_client,
                skill_text="EMPTY-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["state"], "promoted")
            self.assertEqual(event["slices"], 0)
            self.assertEqual(result["summary"]["slices"], 0)
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(len(list((root / "archive" / "fragments").rglob("*.md"))), 2)
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertFalse(any("left in split" in warning for warning in result["warnings"]))

    def test_llm_wrapped_empty_output_reaches_promoted_terminal_state_without_retry_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cached_client = agent_exec.CachingAgentClient(
                FakeAgentClient('{"findings": []}'),
                cache_dir,
            )
            promoter = llm_promoter.LLMPromoter(
                cached_client,
                skill_text="EMPTY-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["state"], "promoted")
            self.assertEqual(event["slices"], 0)
            self.assertEqual(result["summary"]["slices"], 0)
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(len(list((root / "archive" / "fragments").rglob("*.md"))), 2)
            self.assertEqual(list(cache_dir.glob("*.retries")), [])
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            split_or_retry_warnings = [
                warning
                for warning in result["warnings"]
                if "left in split" in warning or "retry " in warning
            ]
            self.assertEqual(split_or_retry_warnings, [])

    def test_llm_promotion_archives_unreferenced_fragments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body a",'
                    '"source_fragment_indices":[0],"relations":[]}]'
                ),
                skill_text="SUBSET-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(len(list((root / "archive" / "fragments").rglob("*.md"))), 2)
            promoted = [e for e in relations.read_edges(root) if e["type"] == "promoted_to"]
            self.assertEqual(len(promoted), 1)
            self.assertEqual(promoted[0]["from"], "fragment:claude__s1__000")

    def test_llm_garbage_leaves_session_split_without_knowledge_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient("not json"),
                skill_text="BROKEN-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            edges = relations.read_edges(root)
            self.assertEqual([e["type"] for e in edges], ["fragment_of", "fragment_of"])
            self.assertTrue(any("left in split" in warning for warning in result["warnings"]))

    def test_llm_relates_to_emits_slice_edge_by_runtime_title(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body a",'
                    '"source_fragment_indices":[0],"relations":[]},'
                    '{"title":"beta","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body b",'
                    '"source_fragment_indices":[1],"relations":[{"type":"relates_to","target_title":"alpha"}]}]'
                ),
                skill_text="RELATES-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(result["summary"]["slices"], 2)
            relate_edges = [e for e in relations.read_edges(root) if e["type"] == "relates_to"]
            self.assertEqual(len(relate_edges), 1)
            self.assertTrue(relate_edges[0]["from"].startswith("slice:sl-"))
            self.assertTrue(relate_edges[0]["to"].startswith("slice:sl-"))

    def test_llm_dangling_relates_to_warns_and_promotes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body a",'
                    '"source_fragment_indices":[0],"relations":[{"type":"relates_to","target_title":"missing"}]}]'
                ),
                skill_text="DANGLING-RELATES-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result["summary"]["slices"], 1)
            relate_edges = [e for e in relations.read_edges(root) if e["type"] == "relates_to"]
            self.assertEqual(relate_edges, [])
            self.assertTrue(any("relates_to target_title" in warning for warning in result["warnings"]))

    def test_promoted_sessions_archive_leftover_fragments_on_resume(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            warnings: list[str] = []
            dry_run_split, _ = pipeline._split_pass(root, cfg, h, "2026-05-31T03:00:00Z", False, warnings)
            self.assertEqual(dry_run_split, 1)
            cached_client = agent_exec.CachingAgentClient(
                FakeAgentClient(
                    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],"body":"body a",'
                    '"source_fragment_indices":[0],"relations":[]}]'
                ),
                root / "runtime" / "cache" / "atomize",
            )
            fragments = [
                pipeline._read_fragment(path)
                for path in sorted((root / "inbox" / "_slices").rglob("*.md"))
            ]
            fragments = [fragment for fragment in fragments if fragment is not None]
            promoter = llm_promoter.LLMPromoter(
                cached_client,
                skill_text="RESUME-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )
            promoter.promote(fragments, cfg)
            self.assertTrue(list((root / "runtime" / "cache" / "atomize").glob("*.json")))
            cache_key = promoter.cache_key_for_fragments(fragments)
            processing.append_state(
                root,
                session_key="claude:s1",
                state="promoted",
                now="2026-05-31T03:01:00Z",
                config_hash=h,
                cache_key=cache_key,
                slices=1,
                promoter="llm",
                model="fake-llm",
                skill_hash="abc123",
            )

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:02:00Z",
                promoter=promoter,
            )

            self.assertEqual(result["summary"]["slices"], 0)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(len(list((root / "archive" / "fragments").rglob("*.md"))), 2)
            self.assertEqual(list((root / "runtime" / "cache" / "atomize").glob("*.json")), [])

    def test_promoted_session_without_leftover_fragments_still_clears_cache_by_ledger_key(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_key = f"claude:s1__{'a' * 64}"
            cache_path = root / "runtime" / "cache" / "atomize" / f"{cache_key}.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("cached", encoding="utf-8")
            processing.append_state(
                root,
                session_key="claude:s1",
                state="promoted",
                now="2026-05-31T03:01:00Z",
                config_hash=h,
                cache_key=cache_key,
                slices=1,
                promoter="llm",
                model="fake-llm",
                skill_hash="abc123",
            )

            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:02:00Z")

            self.assertEqual(result["summary"]["slices"], 0)
            self.assertFalse(cache_path.exists())

    def test_hostile_ledger_cache_key_does_not_delete_outside_cache_root(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "memory"
            cfg, h = atomizer_config.load_config(override_path=None)
            outside = parent / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            processing.append_state(
                root,
                session_key="claude:s1",
                state="promoted",
                now="2026-05-31T03:01:00Z",
                config_hash=h,
                cache_key="../../outside",
                slices=1,
                promoter="llm",
                model="fake-llm",
                skill_hash="abc123",
            )

            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:02:00Z")

            self.assertEqual(result["summary"]["slices"], 0)
            self.assertTrue(outside.exists())

    def test_successful_llm_promotion_clears_session_cache(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cached_client = agent_exec.CachingAgentClient(
                FakeAgentClient(
                    '[{"title":"merged","artifact_kind":"report","project":"paulshaclaw","tags":["alpha"],'
                    '"body":"merged body","source_fragment_indices":[0,1],"relations":[]}]'
                ),
                root / "runtime" / "cache" / "atomize",
            )
            promoter = llm_promoter.LLMPromoter(
                cached_client,
                skill_text="CACHE-CLEAR-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(list((root / "runtime" / "cache" / "atomize").glob("*.json")), [])

    def test_unexpected_promoter_error_leaves_session_split_and_allows_later_sessions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            _seed_raw_s2(root)
            cfg, h = atomizer_config.load_config(override_path=None)

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:00:00Z",
                promoter=ExplodingPromoter(fail_session="s1"),
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(processing.state_of(root, "claude:s2"), "promoted")
            self.assertTrue(any("unexpected promoter failure" in warning for warning in result["warnings"]))

    def test_retry_after_phase3_edge_crash_does_not_duplicate_relations(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            warnings: list[str] = []
            pipeline._split_pass(root, cfg, h, "2026-05-31T03:00:00Z", False, warnings)
            original_append = relations.append_edge
            crashed = {"done": False}

            def flaky_append(*args, **kwargs):
                result = original_append(*args, **kwargs)
                if kwargs.get("type") == "promoted_to" and not crashed["done"]:
                    crashed["done"] = True
                    raise OSError("simulated crash after first promoted_to edge")
                return result

            with mock.patch.object(relations, "append_edge", side_effect=flaky_append):
                with self.assertRaises(OSError):
                    pipeline._promote_pass(
                        root,
                        cfg,
                        h,
                        "2026-05-31T03:01:00Z",
                        False,
                        pipeline.IdentityPromoter(),
                        [],
                        {},
                    )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:02:00Z")

            self.assertEqual(result["summary"]["slices"], 2)
            edges = relations.read_edges(root)
            edge_triples = [(edge["type"], edge["from"], edge["to"]) for edge in edges]
            self.assertEqual(len(edge_triples), len(set(edge_triples)))

    def test_bad_fragment_references_leave_session_split_without_partial_writes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:00:00Z",
                promoter=BrokenReferencePromoter(),
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            edges = relations.read_edges(root)
            self.assertEqual([e["type"] for e in edges], ["fragment_of", "fragment_of"])
            self.assertTrue(any("left in split" in warning for warning in result["warnings"]))

    def test_unsupported_semantic_relation_leaves_session_split_without_partial_writes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:00:00Z",
                promoter=UnsupportedRelationPromoter(),
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            edges = relations.read_edges(root)
            self.assertEqual([e["type"] for e in edges], ["fragment_of", "fragment_of"])
            self.assertTrue(any("unsupported semantic relation type" in w for w in result["warnings"]))

    def test_partial_promote_failure_then_retry(self):
        """
        Regression test for Task 7 review findings:
        1. If fragment N fails validation, fragments 0..N-1 should not have written slices/relations
        2. One bad split session should not block later split sessions
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)  # claude:s1
            _seed_raw_s2(root)  # claude:s2
            cfg, h = atomizer_config.load_config(override_path=None)

            # First run: split+promote with failing promoter that breaks s1 fragment 1
            # This should split both, but only promote s2 (s1 fails validation and stays split)
            kwargs = dict(config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            failing_promoter = FailingPromoter(fail_session="s1", fail_index=1)
            result = pipeline.run(root, **kwargs, promoter=failing_promoter)

            # Verify s1 is still split (failed validation)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")

            # Verify s2 is promoted (should not be blocked by s1 failure)
            self.assertEqual(processing.state_of(root, "claude:s2"), "promoted")

            # Verify no knowledge slices for s1
            s1_slices = [
                p
                for p in (root / "knowledge" / "paulshaclaw").rglob("*.md")
                if "s1" in p.read_text()
            ]
            self.assertEqual(len(s1_slices), 0, "s1 should not have any knowledge slices after failed promotion")

            # Verify s2 has knowledge slices
            s2_slices = [
                p
                for p in (root / "knowledge" / "paulshaclaw").rglob("*.md")
                if "s2" in p.read_text()
            ]
            self.assertGreater(len(s2_slices), 0, "s2 should have knowledge slices")

            # Verify no promoted_to or distilled_from edges for s1
            edges = relations.read_edges(root)
            s1_promoted_edges = [e for e in edges if e["type"] == "promoted_to" and "__s1__" in e["from"]]
            s1_distilled_edges = [e for e in edges if e["type"] == "distilled_from" and "claude:s1" in e["to"]]
            self.assertEqual(len(s1_promoted_edges), 0, "s1 should not have promoted_to edges after failed promotion")
            self.assertEqual(len(s1_distilled_edges), 0, "s1 should not have distilled_from edges after failed promotion")

            # Verify warning was issued
            self.assertGreater(len(result["warnings"]), 0, "should have warning about failed validation")
            self.assertTrue(
                any("left in split" in w for w in result["warnings"]),
                "warning should mention leaving session in split",
            )

            # Second run: retry with normal promoter (should promote s1 without duplicates)
            result2 = pipeline.run(root, **kwargs)

            # Verify s1 is now promoted
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")

            # Verify no duplicate edges: check that (type, from, to) triples are unique
            edges = relations.read_edges(root)
            edge_triples = [(e["type"], e["from"], e["to"]) for e in edges]
            self.assertEqual(
                len(edge_triples),
                len(set(edge_triples)),
                f"duplicate relation edges detected: {len(edge_triples)} total, {len(set(edge_triples))} unique",
            )


    def test_structural_echo_slice_is_dropped_as_noise(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "sessions" / "claude" / "2026-06-25" / "s9.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(
                "---\nmemory_layer: inbox\nproject: paulshaclaw\nsource_agent: claude\n"
                "source_session: s9\nsource_artifact: session\n"
                'captured_at: "2026-06-25T00:00:00Z"\n'
                "provenance:\n  repo: r\n  commit: c\n  path: p\n---\n"
                "## Summary\n使用者招呼與啟動 session\n"
                "## Real Topic\n這是一段足夠長的真實技術內容，描述某個具體結論與其理由說明。\n",
                encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-06-25T03:00:00Z")
            # The ## Summary fragment is structural-echo noise → dropped; ## Real Topic kept.
            # (atomize writes by slice_id; the title-- prefix is added later by the moc rename
            # pass, so assert on body content, not filename.)
            self.assertEqual(result["summary"]["noise_dropped"], 1)
            self.assertEqual(result["summary"]["slices"], 1)
            kept = list((root / "knowledge").rglob("*.md"))
            self.assertEqual(len(kept), 1)
            kept_body = kept[0].read_text(encoding="utf-8")
            self.assertIn("真實技術內容", kept_body)
            self.assertNotIn("使用者招呼與啟動 session", kept_body)

    def test_noise_drops_do_not_inflate_skipped_or_warnings(self):
        # #139 finding 1: intentional noise drops must NOT count as health-affecting
        # `skipped`/`warnings`, else a normal noise-filtering run looks degraded (partial).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "sessions" / "claude" / "2026-06-25" / "s11.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(
                "---\nmemory_layer: inbox\nproject: paulshaclaw\nsource_agent: claude\n"
                "source_session: s11\nsource_artifact: session\n"
                'captured_at: "2026-06-25T00:00:00Z"\n'
                "provenance:\n  repo: r\n  commit: c\n  path: p\n---\n"
                "## CWD\n/home/paul_chen\n## Touched files\n- (none)\n",
                encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-06-25T03:00:00Z")
            self.assertGreaterEqual(result["summary"]["noise_dropped"], 1)
            self.assertEqual(result["summary"]["skipped"], 0)
            self.assertFalse(any("noise" in w for w in result["warnings"]))

    def test_dry_run_counts_noise_without_writing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "sessions" / "claude" / "2026-06-25" / "s10.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(
                "---\nmemory_layer: inbox\nproject: paulshaclaw\nsource_agent: claude\n"
                "source_session: s10\nsource_artifact: session\n"
                'captured_at: "2026-06-25T00:00:00Z"\n'
                "provenance:\n  repo: r\n  commit: c\n  path: p\n---\n"
                "## CWD\n/home/paul_chen\n"
                "## Real Topic\n這是一段足夠長的真實技術內容，描述某個具體結論與其理由說明。\n",
                encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)
            result = pipeline.run(root, config=cfg, config_hash=h, now="2026-06-25T03:00:00Z", dry_run=True)
            self.assertEqual(result["summary"]["noise_dropped"], 1)   # ## CWD structural-echo
            self.assertEqual(result["summary"]["slices"], 1)          # ## Real Topic kept
            self.assertFalse(list((root / "knowledge").rglob("*.md")))  # dry-run writes nothing


_RAW_TITLED = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-05-31T00:00:00Z"
title: "修正啟動鏈"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha body
# Topic B
beta body
"""


def _seed_raw_titled(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW_TITLED, encoding="utf-8")
    return raw


class LLMPromoteEndToEndTests(unittest.TestCase):
    def test_llm_promote_persists_titles_in_frontmatter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw_titled(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            skill_text = "TITLE-SKILL"
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient(
                    '[{"title":"OOM 風險","artifact_kind":"report","project":"paulshaclaw",'
                    '"tags":["oom"],"body":"distilled body","source_fragment_indices":[0,1],'
                    '"relations":[]}]'
                ),
                skill_text=skill_text,
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            written = list((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertEqual(len(written), 1)
            fm, _ = fio.read(written[0].read_text(encoding="utf-8"))
            self.assertEqual(fm["session_title"], "修正啟動鏈")
            self.assertEqual(fm["atom_title"], "OOM 風險")
            self.assertEqual(fm["distilled_from"], "claude:s1")

            promoted_event = processing.read_events(root)[-1]
            self.assertEqual(promoted_event["promoter"], "llm")
            self.assertEqual(promoted_event["model"], "fake-llm")
            self.assertEqual(
                promoted_event["skill_hash"],
                hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
            )

    def test_llm_promote_fail_closed_leaves_session_split_and_writes_no_slices(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw_titled(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(
                FakeAgentClient("not valid json at all"),
                skill_text="BROKEN-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z", promoter=promoter
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(list((root / "knowledge").rglob("*.md")), [])
            self.assertEqual(result["summary"]["slices"], 0)
            self.assertTrue(any("left in split" in warning for warning in result["warnings"]))


class ReimportOverwriteTests(unittest.TestCase):
    def test_reimport_overwrites_renamed_file_no_duplicate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            # simulate a moc-renamed existing slice
            existing = kdir / "alpha--sl-xyz.md"
            existing.write_text("---\nslice_id: sl-xyz\nmemory_layer: knowledge\nproject: paulshaclaw\n---\nOLD\n", encoding="utf-8")
            # atomize must resolve the write path for slice_id sl-xyz to the existing renamed file
            from paulsha_hippo.atomizer import pipeline
            resolved = pipeline._knowledge_path_for(root, "paulshaclaw", "sl-xyz")
            self.assertEqual(resolved, existing)
            # and for a brand-new slice_id it falls back to <slice_id>.md
            fresh = pipeline._knowledge_path_for(root, "paulshaclaw", "sl-new")
            self.assertEqual(fresh.name, "sl-new.md")


class PromoteFailureCacheRecoveryTests(unittest.TestCase):
    """#174: failed LLM promotion must not leave a replayable poisoned cache behind."""

    def _cached_llm_promoter(
        self, root: Path, outputs: list[str | Exception]
    ) -> tuple[ScriptedAgentClient, llm_promoter.LLMPromoter]:
        inner = ScriptedAgentClient(outputs)
        cached = agent_exec.CachingAgentClient(
            inner,
            root / "runtime" / "cache" / "atomize",
        )
        promoter = llm_promoter.LLMPromoter(
            cached,
            skill_text="RECOVERY-SKILL",
            known_projects=["paulshaclaw"],
            model="fake-llm",
        )
        return inner, promoter

    def _split_and_cache_key(self, root: Path, cfg, h) -> str:
        split_warnings: list[str] = []
        pipeline._split_pass(root, cfg, h, "2026-07-02T02:00:00Z", False, split_warnings)
        fragments = [
            pipeline._read_fragment(path)
            for path in sorted((root / "inbox" / "_slices").rglob("*.md"))
        ]
        fragments = [fragment for fragment in fragments if fragment is not None]
        return llm_promoter.LLMPromoter.cache_key_for_fragments(fragments)

    def test_promote_error_clears_poisoned_cache(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(root, ["chatter, not json"])

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T03:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertTrue(any("left in split" in warning for warning in result["warnings"]))
            self.assertEqual(
                list((root / "runtime" / "cache" / "atomize").glob("*.json")),
                [],
            )

    def test_promote_retry_reinvokes_llm_and_recovers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                ["chatter, not json", _VALID_ONE_SLICE],
            )

            pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T03:00:00Z",
                promoter=promoter,
            )

            result2 = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T04:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 2)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result2["summary"]["slices"], 1)

    def test_non_llm_promoter_failure_does_not_touch_cache_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cache_dir.mkdir(parents=True)
            sentinel = cache_dir / "keep.json"
            sentinel.write_text("keep", encoding="utf-8")

            pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T03:00:00Z",
                promoter=ExplodingPromoter(fail_session="s1"),
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertTrue(sentinel.exists())
            self.assertEqual(sorted(path.name for path in cache_dir.iterdir()), ["keep.json"])

    def test_transport_failures_do_not_consume_retry_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentExecError("agent timed out after 600s")] * 6,
            )

            result = None
            for hour in range(6):
                result = pipeline.run(
                    root,
                    config=cfg,
                    config_hash=h,
                    now=f"2026-07-02T0{hour}:00:00Z",
                    promoter=promoter,
                )

            cache_dir = root / "runtime" / "cache" / "atomize"
            self.assertIsNotNone(result)
            self.assertEqual(inner.calls, 6)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(list(cache_dir.glob("*.retries")), [])
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertTrue(any("transport failure" in warning for warning in result["warnings"]))
            self.assertFalse(any("poisoned cache retained" in warning for warning in result["warnings"]))

    def test_transport_recovery_chatter_starts_content_retry_budget_at_one(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentExecError("agent timed out after 600s")] * 6
                + ["chatter, not json", _VALID_ONE_SLICE],
            )

            for hour in range(6):
                pipeline.run(
                    root,
                    config=cfg,
                    config_hash=h,
                    now=f"2026-07-02T0{hour}:00:00Z",
                    promoter=promoter,
                )

            cache_dir = root / "runtime" / "cache" / "atomize"
            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T06:00:00Z",
                promoter=promoter,
            )

            retries = list(cache_dir.glob("*.retries"))
            self.assertEqual(inner.calls, 7)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0].read_text(encoding="utf-8").strip(), "1")
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertTrue(any("retry 1/5" in warning for warning in result["warnings"]))

            result2 = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T07:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 8)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result2["summary"]["slices"], 1)

    def test_retry_counter_increments_and_cache_cleared_within_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(root, ["chatter, not json"])

            pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T03:00:00Z",
                promoter=promoter,
            )

            cache_dir = root / "runtime" / "cache" / "atomize"
            retries = list(cache_dir.glob("*.retries"))
            self.assertEqual(inner.calls, 1)
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0].read_text(encoding="utf-8").strip(), "1")
            self.assertEqual(list(cache_dir.glob("*.json")), [])

    def test_exhausted_budget_retains_poisoned_cache_and_stops_llm_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cache_dir.mkdir(parents=True)
            cache_key = self._split_and_cache_key(root, cfg, h)
            (cache_dir / f"{cache_key}.retries").write_text("5", encoding="utf-8")
            inner, promoter = self._cached_llm_promoter(root, ["chatter, not json"])

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T04:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 1)
            self.assertEqual(
                (cache_dir / f"{cache_key}.retries").read_text(encoding="utf-8").strip(),
                "6",
            )
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 1)
            self.assertTrue(any("retry budget exhausted" in warning for warning in result["warnings"]))

            result2 = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T05:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertTrue(any("left in split" in warning for warning in result2["warnings"]))

    def test_successful_promotion_removes_retry_sidecar(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cache_dir.mkdir(parents=True)
            cache_key = self._split_and_cache_key(root, cfg, h)
            sidecar = cache_dir / f"{cache_key}.retries"
            sidecar.write_text("3", encoding="utf-8")
            inner, promoter = self._cached_llm_promoter(root, [_VALID_ONE_SLICE])

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T04:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(inner.calls, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result["summary"]["slices"], 1)
            self.assertFalse(sidecar.exists())
            self.assertEqual(list(cache_dir.glob("*.json")), [])

    def test_dry_run_existing_split_session_leaves_retry_budget_and_cache_untouched(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cache_dir.mkdir(parents=True)
            cache_key = self._split_and_cache_key(root, cfg, h)
            cache_path = cache_dir / f"{cache_key}.json"
            sidecar = cache_dir / f"{cache_key}.retries"
            cache_path.write_text("chatter, not json", encoding="utf-8")
            sidecar.write_text("1", encoding="utf-8")
            inner, promoter = self._cached_llm_promoter(root, [_VALID_ONE_SLICE])

            pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-07-02T05:00:00Z",
                promoter=promoter,
                dry_run=True,
            )

            self.assertEqual(inner.calls, 0)
            self.assertTrue(cache_path.exists())
            self.assertEqual(sidecar.read_text(encoding="utf-8").strip(), "1")
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")


if __name__ == "__main__":
    unittest.main()
