from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import config as janitor_config
from paulsha_hippo.janitor import scanner
from paulsha_hippo.ledger import lifecycle, retrieval_set

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "knowledge"
PATH_OK = lambda record: True


def _cfg():
    return janitor_config.load_config(override_path=None)


def _seed_import(root: Path, key: str, status: str, ts: str) -> None:
    path = root / "runtime" / "ledger" / "import.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"idempotency_key": key, "status": status, "recorded_at": ts}) + "\n")


class JanitorE2ETests(unittest.TestCase):
    def test_scenario_a_ttl_decay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=h,
                             now="2026-05-31T00:00:00Z", source_path_exists=PATH_OK)
            self.assertEqual(retrieval_set.active_records(root, ["sl-ttl"]), [])

    def test_scenario_b_superseded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "superseded", kroot)
            cfg, h = _cfg()
            scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=h,
                             now="2026-05-31T00:00:00Z", source_path_exists=PATH_OK)
            decayed = [e for e in lifecycle.read_events(root) if e["record_id"] == "sl-old"]
            self.assertEqual(decayed[0]["reason"], "superseded")
            self.assertEqual(decayed[0]["metadata"]["detail"]["superseded_by"], "sl-new")
            self.assertEqual(retrieval_set.active_records(root, ["sl-old", "sl-new"]), ["sl-new"])

    def test_scenario_c_source_invalid(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=h,
                             now="2020-02-01T00:00:00Z",  # before TTL (captured 2020-01-01 + 90d)
                             source_path_exists=lambda record: False)
            decayed = lifecycle.read_events(root)
            self.assertEqual(decayed[0]["reason"], "source_invalid")

    def test_scenario_d_reactivation_cycle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            kwargs = dict(knowledge_root=kroot, config=cfg, config_hash=h, source_path_exists=PATH_OK)
            scanner.run_scan(root, now="2026-05-31T00:00:00Z", **kwargs)  # decays
            self.assertEqual(retrieval_set.record_state(root, "sl-ttl"), "decayed")
            _seed_import(root, "claude:sess-ttl", "updated", "2026-06-01T00:00:00Z")
            scanner.run_scan(root, now="2026-06-02T00:00:00Z", **kwargs)  # reactivates
            self.assertEqual(retrieval_set.record_state(root, "sl-ttl"), "active")
            events = lifecycle.read_events(root)
            self.assertEqual(events[-1]["event_type"], "reactivation")
            self.assertEqual(events[-1]["metadata"]["agent_ref"], "claude:sess-ttl")

    def test_scenario_e_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            kwargs = dict(knowledge_root=kroot, config=cfg, config_hash=h,
                          now="2026-05-31T00:00:00Z", source_path_exists=PATH_OK)
            scanner.run_scan(root, **kwargs)
            scanner.run_scan(root, **kwargs)
            self.assertEqual(len(lifecycle.read_events(root)), 1)

    def test_scenario_f_anti_flap(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            kwargs = dict(knowledge_root=kroot, config=cfg, config_hash=h, source_path_exists=PATH_OK)
            scanner.run_scan(root, now="2026-05-31T00:00:00Z", **kwargs)
            _seed_import(root, "claude:sess-ttl", "updated", "2026-06-01T00:00:00Z")
            scanner.run_scan(root, now="2026-06-02T00:00:00Z", **kwargs)  # reactivate
            n_before = len(lifecycle.read_events(root))
            scanner.run_scan(root, now="2026-06-03T00:00:00Z", **kwargs)  # must NOT re-decay
            self.assertEqual(len(lifecycle.read_events(root)), n_before)
            self.assertEqual(retrieval_set.record_state(root, "sl-ttl"), "active")

    def test_lifecycle_ledger_has_no_record_body(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=h,
                             now="2026-05-31T00:00:00Z", source_path_exists=PATH_OK)
            raw = (root / "runtime" / "ledger" / "lifecycle.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("body", raw)

    def test_persisted_event_ts_uses_injected_now_not_wall_clock(self):
        # The ledger's temporal ordering must come from the scan's logical `now`,
        # otherwise anti-flap base and reactivation cutoff drift with wall-clock.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            scanner.run_scan(root, knowledge_root=kroot, config=cfg, config_hash=h,
                             now="2099-01-01T00:00:00Z", source_path_exists=PATH_OK)
            events = lifecycle.read_events(root)
            self.assertEqual(events[0]["ts"], "2099-01-01T00:00:00Z")

    def test_hash_duplicate_reimport_does_not_reactivate(self):
        # A no-op re-import (hash-duplicate) carries no new evidence and must NOT
        # revive a decayed record; only written/updated may reactivate.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kroot = root / "knowledge"
            shutil.copytree(FIXTURES / "ttl", kroot)
            cfg, h = _cfg()
            kwargs = dict(knowledge_root=kroot, config=cfg, config_hash=h, source_path_exists=PATH_OK)
            scanner.run_scan(root, now="2026-05-31T00:00:00Z", **kwargs)  # decays
            self.assertEqual(retrieval_set.record_state(root, "sl-ttl"), "decayed")
            _seed_import(root, "claude:sess-ttl", "hash-duplicate", "2026-06-01T00:00:00Z")
            scanner.run_scan(root, now="2026-06-02T00:00:00Z", **kwargs)
            self.assertEqual(retrieval_set.record_state(root, "sl-ttl"), "decayed")


if __name__ == "__main__":
    unittest.main()
