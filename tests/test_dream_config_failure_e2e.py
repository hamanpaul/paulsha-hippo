"""#15 review F2 E2E：atomizer config 無效（malformed override）時，dream run
不得讓例外逃出失敗邊界——eligible split sessions 立即 park（含證據）、
dream ledger 記 error record、process 正常收斂（exit 0），timer 不再整輪重複失敗。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import cli
from paulsha_hippo.ledger import dream as dream_ledger
from paulsha_hippo.ledger import processing

_MALFORMED_OVERRIDE = (
    'schema_version: "1"\n'
    "agent_exec:\n"
    "  command: not-a-list\n"  # 必須是 list → AtomizerConfigError
)


class DreamConfigFailureE2ETests(unittest.TestCase):
    def _env(self, tmp: str) -> dict[str, str]:
        return {
            "PSC_CONFIG_ROOT": f"{tmp}/cfg/.config/paulshaclaw",
            "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            "PSC_MEMORY_ROOT": f"{tmp}/memory",
        }

    def _write_malformed_override(self, tmp: str) -> None:
        cfg_dir = Path(tmp) / "cfg" / ".config" / "paulshaclaw"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "atomizer.override.yaml").write_text(
            _MALFORMED_OVERRIDE, encoding="utf-8"
        )

    def _seed_split_session(self, root: Path) -> None:
        processing.append_state(
            root, session_key="claude:s1", state="split",
            now="2026-07-09T00:00:00Z", config_hash="h", fragments=2,
        )

    def test_malformed_override_parks_split_sessions_and_records_error(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            root.mkdir(parents=True)
            self._write_malformed_override(tmp)
            self._seed_split_session(root)

            buf = io.StringIO()
            with mock.patch.dict(os.environ, self._env(tmp)), redirect_stdout(buf):
                rc = cli.main(["dream", "run", "--memory-root", str(root),
                               "--now", "2026-07-10T00:00:00Z"])

            # 例外不得逃出：process 正常收斂，失敗以 record 形式呈現
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "failed")
            self.assertIn("atomize:AtomizerConfigError", payload["errors"])
            atomize = payload["passes"]["atomize"]
            self.assertEqual(atomize["error"], "AtomizerConfigError")
            self.assertIn("agent_exec.command", atomize["error_message"])

            # spec「config 無效立即 parked」：failure category＋證據齊備
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            event = processing.fold_events(root)["claude:s1"]
            self.assertEqual(event["failure_category"], "backend_unavailable")
            self.assertIn("agent_exec.command", event["error"])
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            self.assertTrue(evidence.exists())
            evidence_payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(
                evidence_payload["failure_category"], "backend_unavailable"
            )

            # dream ledger 有 error record（timer 面可觀測）
            last = dream_ledger.last_run(root)
            self.assertIsNotNone(last)
            self.assertEqual(last["status"], "failed")

            # janitor 不因 atomizer config 壞掉而連坐（pass 隔離）
            self.assertNotIn("error", payload["passes"]["janitor"])

    def test_second_run_does_not_double_park(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            root.mkdir(parents=True)
            self._write_malformed_override(tmp)
            self._seed_split_session(root)

            for round_now in ("2026-07-10T00:00:00Z", "2026-07-10T01:00:00Z"):
                buf = io.StringIO()
                with mock.patch.dict(os.environ, self._env(tmp)), redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root),
                                   "--now", round_now])
                self.assertEqual(rc, 0)

            parked_events = [
                event for event in processing.read_events(root)
                if event.get("state") == "parked"
            ]
            self.assertEqual(len(parked_events), 1)

    def test_dry_run_with_malformed_override_is_mutation_free(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            root.mkdir(parents=True)
            self._write_malformed_override(tmp)
            self._seed_split_session(root)

            buf = io.StringIO()
            with mock.patch.dict(os.environ, self._env(tmp)), redirect_stdout(buf):
                rc = cli.main(["dream", "run", "--memory-root", str(root),
                               "--now", "2026-07-10T00:00:00Z", "--dry-run"])

            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "failed")
            # dry-run 不得 park、不得寫 dream ledger
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertIsNone(dream_ledger.last_run(root))
            self.assertFalse(
                (root / "runtime" / "queue" / "_failed" / "claude__s1.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
