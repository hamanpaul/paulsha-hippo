from __future__ import annotations

import io
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli as memory_cli
from paulsha_hippo import paths
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import pipeline
from paulsha_hippo.atomizer.agent_exec import FakeAgentClient
from paulsha_hippo.atomizer.llm_promoter import LLMPromoter
from paulsha_hippo.ledger import processing, relations
from paulsha_hippo.lib.lifecycle.schema import parse_artifact_text, validate_frontmatter

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, raw)
    return raw


def _cfg():
    return atomizer_config.load_config(override_path=None)


class AtomizerE2ETests(unittest.TestCase):
    def test_full_run_then_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "promoted")
            r2 = pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T04:00:00Z")
            self.assertEqual(r2["summary"]["slices"], 0)

    def test_crash_resume_promote_completes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            warnings: list[str] = []
            pipeline._split_pass(root, cfg, h, "2026-05-31T03:00:00Z", False, warnings)
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "split")
            self.assertTrue(list((root / "inbox" / "_slices").rglob("*.md")))
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:30:00Z")
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "promoted")
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])

    def test_reimport_ambiguous_identity_titles_stay_parallel_for_review(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            slice_files = list((root / "knowledge").rglob("*.md"))
            ids_before = sorted(p.name for p in slice_files)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(FIXTURE.read_text(encoding="utf-8").replace("alpha body", "ALPHA v2"),
                           encoding="utf-8")
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T05:00:00Z")
            ids_after = sorted(p.name for p in (root / "knowledge").rglob("*.md"))
            self.assertTrue(set(ids_before).issubset(ids_after))
            new_ids = sorted(set(ids_after) - set(ids_before))
            self.assertEqual(len(new_ids), 1)
            new_doc = parse_artifact_text(
                (root / "knowledge" / "paulshaclaw" / new_ids[0]).read_text(encoding="utf-8")
            )
            self.assertEqual(new_doc.frontmatter["supersedes"], "[]")
            supersedes = [edge for edge in relations.read_edges(root) if edge["type"] == "supersedes"]
            self.assertEqual(supersedes, [])
            self.assertIn(
                "ALPHA v2",
                (root / "knowledge" / "paulshaclaw" / new_ids[0]).read_text(),
            )

    def test_produced_slice_passes_stage3_gate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            slice_path = next((root / "knowledge").rglob("*.md"))
            doc = parse_artifact_text(slice_path.read_text(encoding="utf-8"))
            result = validate_frontmatter(frontmatter=doc.frontmatter, body=doc.body)
            self.assertTrue(result.ok, result.errors)

    def test_relations_have_distilled_from(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            edges = relations.neighbors(root, "session:claude:sess-e2e")
            self.assertTrue(any(e["type"] == "distilled_from" for e in edges))

    def test_ledgers_have_no_record_body(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            pipeline.run(root, config=cfg, config_hash=h, now="2026-05-31T03:00:00Z")
            for name in ("processing.jsonl", "relations.jsonl"):
                raw = (root / "runtime" / "ledger" / name).read_text(encoding="utf-8")
                self.assertNotIn("alpha body", raw)
                self.assertNotIn("beta body", raw)

    def test_llm_fake_run_writes_gate_valid_slices_without_raw_body_leakage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            cfg, h = _cfg()
            promoter = LLMPromoter(
                FakeAgentClient(
                    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":["t1"],'
                    '"body":"alpha distilled","source_fragment_indices":[0],'
                    '"relations":[{"type":"mentions","entity":"MTK"}]},'
                    '{"title":"beta","artifact_kind":"report","project":"paulshaclaw","tags":["t2"],'
                    '"body":"beta distilled","source_fragment_indices":[1],'
                    '"relations":[{"type":"relates_to","target_title":"alpha"}]}]'
                ),
                skill_text="LLM-FAKE-SKILL",
                known_projects=["paulshaclaw"],
                model="fake-llm",
            )

            result = pipeline.run(
                root,
                config=cfg,
                config_hash=h,
                now="2026-05-31T03:00:00Z",
                promoter=promoter,
            )

            self.assertEqual(result["summary"]["slices"], 2)
            slice_paths = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertEqual(len(slice_paths), 2)
            for slice_path in slice_paths:
                doc = parse_artifact_text(slice_path.read_text(encoding="utf-8"))
                gate = validate_frontmatter(frontmatter=doc.frontmatter, body=doc.body)
                self.assertTrue(gate.ok, gate.errors)

            edge_types = {edge["type"] for edge in relations.read_edges(root)}
            self.assertIn("mentions", edge_types)
            self.assertIn("relates_to", edge_types)

            for name in ("processing.jsonl", "relations.jsonl"):
                ledger = (root / "runtime" / "ledger" / name).read_text(encoding="utf-8")
                self.assertNotIn("alpha body", ledger)
                self.assertNotIn("beta body", ledger)

    def test_llm_cli_stub_run_writes_gate_valid_slice_with_flow_through(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            stub = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"
            import yaml
            canonical = paths.atomizer_config_path()
            document = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            document["known_projects_file"] = str(projects)
            document["external_agents"]["profiles"] = [{
                "id": "fake-agent", "enabled": True, "tier": 1, "priority": 1,
                "traits": ["test"], "task_classes": ["atomization"],
                "model": "fake", "supported_models": ["fake"],
                "effort": "medium", "supported_efforts": ["medium"],
                "argv": [sys.executable, str(stub)],
            }]
            canonical.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
            buf = io.StringIO()

            with redirect_stdout(buf):
                rc = memory_cli.main(["atomize",
                        "--memory-root",
                        str(root),
                        "--now",
                        "2026-05-31T03:00:00Z",
                        "--promoter",
                        "llm",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertIn('"slices": 1', buf.getvalue())
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "promoted")
            slice_path = next((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            doc = parse_artifact_text(slice_path.read_text(encoding="utf-8"))
            gate = validate_frontmatter(frontmatter=doc.frontmatter, body=doc.body)
            self.assertTrue(gate.ok, gate.errors)
            self.assertEqual(doc.frontmatter["source_session"], "sess-e2e")
            self.assertEqual(doc.frontmatter["distilled_from"], "claude:sess-e2e")


if __name__ == "__main__":
    unittest.main()
