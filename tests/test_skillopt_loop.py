from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.skillopt.loop import SkillOptError, optimize_skill

VALID = "---\nname: s\n---\nbody\n"
BETTER = "---\nname: s\n---\nbetter body\n"
_SANDBOX_ROOT = Path(__file__).resolve().parent / ".skillopt-loop-sandbox"
_ADAPTER = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "skillopt" / "codex_exec_acp_adapter.py"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestOptimizeSkill(unittest.TestCase):
    def setUp(self) -> None:
        if _SANDBOX_ROOT.exists():
            shutil.rmtree(_SANDBOX_ROOT)
        self.root = _SANDBOX_ROOT / self.id()
        self.root.mkdir(parents=True, exist_ok=True)
        self.skill = _write(self.root / "atomize-knowledge-slice.md", VALID)
        self.train = [{"id": "t1", "input": "i", "gold": "g"}]
        self.val = [{"id": "v1", "input": "i", "gold": "g"}]

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        if _SANDBOX_ROOT.exists() and not any(_SANDBOX_ROOT.iterdir()):
            _SANDBOX_ROOT.rmdir()

    def _rollout(self, skill_text: str, _input: str) -> str:
        return skill_text

    def test_accept_when_candidate_strictly_better(self) -> None:
        score = lambda out, gold: 1.0 if out == BETTER else 0.0
        res = optimize_skill(
            self.skill,
            rollout=self._rollout,
            score=score,
            train_set=self.train,
            val_set=self.val,
            optimizer=lambda text, failures: BETTER,
            budget=1,
            now="2026-06-04T00:00:00Z",
            record_path=self.root / "rec.jsonl",
        )
        self.assertTrue(res["accepted"])
        self.assertEqual(self.skill.read_text(encoding="utf-8"), BETTER)
        self.assertIn("history_backup", res)
        rec = json.loads((self.root / "rec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(rec["accepted"])
        self.assertNotIn("input", rec)

    def test_record_append_failure_after_accept_rolls_back_skill(self) -> None:
        # Durability gate (vendored evolve behavior): an accepted candidate is written
        # ONLY if its record durably appends; if the record append raises afterwards,
        # the skill MUST be rolled back so the ledger never claims an unrecorded change.
        with mock.patch(
            "paulsha_hippo.skillopt.loop._append_record",
            side_effect=OSError("disk full"),
        ):
            res = optimize_skill(
                self.skill,
                rollout=self._rollout,
                score=lambda out, gold: 1.0 if out == BETTER else 0.0,
                train_set=self.train,
                val_set=self.val,
                optimizer=lambda text, failures: BETTER,
                budget=1,
                now="2026-06-04T00:00:00Z",
                record_path=self.root / "rec.jsonl",
            )
        self.assertEqual(res["reason"], "error")
        self.assertFalse(res["accepted"])
        self.assertNotIn("rollback_failed", res)
        self.assertEqual(self.skill.read_text(encoding="utf-8"), VALID)

    def test_reject_no_improvement_leaves_skill_unchanged(self) -> None:
        res = optimize_skill(
            self.skill,
            rollout=self._rollout,
            score=lambda o, g: 0.5,
            train_set=self.train,
            val_set=self.val,
            optimizer=lambda t, f: VALID,
            budget=1,
            now="2026-06-04T00:00:00Z",
        )
        self.assertFalse(res["accepted"])
        self.assertEqual(self.skill.read_text(encoding="utf-8"), VALID)

    def test_reject_invalid_candidate(self) -> None:
        res = optimize_skill(
            self.skill,
            rollout=self._rollout,
            score=lambda o, g: 1.0 if o == BETTER else 0.0,
            train_set=self.train,
            val_set=self.val,
            optimizer=lambda t, f: "no frontmatter",
            budget=1,
            now="2026-06-04T00:00:00Z",
        )
        self.assertFalse(res["accepted"])
        self.assertEqual(self.skill.read_text(encoding="utf-8"), VALID)

    def test_empty_val_raises(self) -> None:
        with self.assertRaises(SkillOptError):
            optimize_skill(
                self.skill,
                rollout=self._rollout,
                score=lambda o, g: 1.0,
                train_set=self.train,
                val_set=[],
                optimizer=lambda t, f: BETTER,
                budget=1,
                now="2026-06-04T00:00:00Z",
            )

    def test_rollout_exception_fails_closed_unchanged(self) -> None:
        def boom(skill_text: str, _input: str) -> str:
            raise RuntimeError("rollout boom")

        res = optimize_skill(
            self.skill,
            rollout=boom,
            score=lambda o, g: 1.0,
            train_set=self.train,
            val_set=self.val,
            optimizer=lambda t, f: BETTER,
            budget=1,
            now="2026-06-04T00:00:00Z",
        )
        self.assertEqual(res["reason"], "error")
        self.assertIsNone(res["baseline_score"])
        self.assertEqual(self.skill.read_text(encoding="utf-8"), VALID)

    def test_record_append_and_restore_both_fail_flags_rollback_failed(self) -> None:
        # Vendored evolve contract: an accepted candidate is written first; if the
        # record append fails AND the restore also fails, the skill is left mutated
        # (best_text) and the result is flagged rollback_failed=True so the unrecorded
        # mutation is surfaced rather than hidden.
        with (
            mock.patch("paulsha_hippo.skillopt.loop._append_record", side_effect=RuntimeError("ledger down")),
            mock.patch("paulsha_hippo.skillopt.loop._restore_original_skill", side_effect=RuntimeError("restore down")),
        ):
            res = optimize_skill(
                self.skill,
                rollout=self._rollout,
                score=lambda out, gold: 1.0 if out == BETTER else 0.0,
                train_set=self.train,
                val_set=self.val,
                optimizer=lambda text, failures: BETTER,
                budget=1,
                now="2026-06-04T00:00:00Z",
                record_path=self.root / "rec.jsonl",
            )

        self.assertEqual(res["reason"], "error")
        self.assertTrue(res["rollback_failed"])
        self.assertEqual(self.skill.read_text(encoding="utf-8"), BETTER)

    def test_vendored_acp_adapter_handles_initialize_and_session_new_from_isolated_copy(self) -> None:
        isolated_adapter = self.root / "isolated" / "codex_exec_acp_adapter.py"
        isolated_adapter.parent.mkdir(parents=True, exist_ok=True)
        isolated_adapter.write_text(_ADAPTER.read_text(encoding="utf-8"), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(isolated_adapter)],
            input="\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "session/new",
                            "params": {"cwd": str(self.root)},
                        }
                    ),
                    "",
                ]
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(responses[0]["result"]["agentInfo"]["name"], "codex-exec-acp-adapter")
        self.assertEqual(responses[1]["id"], 2)
        self.assertTrue(responses[1]["result"]["sessionId"].startswith("sess_"))


if __name__ == "__main__":
    unittest.main()
