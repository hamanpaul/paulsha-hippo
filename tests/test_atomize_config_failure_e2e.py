"""#15 Codex 複驗 blocking E2E：直呼 `hippo atomize`（非 dream 路徑）的初始化
失敗（config 無效／promoter 建構失敗）也必須接上 park 鏈——spec「config 無效
立即 parked」不分入口：eligible split sessions 立即 park（含 `_failed/` 證據），
CLI 以結構化錯誤收斂（exit 1），不得 traceback 逃逸、session 卡在 split。
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
from paulsha_hippo.ledger import processing

_MALFORMED_CONFIG = (
    'schema_version: "1"\n'
    "external_agents:\n"
    "  profiles: not-a-list\n"
)


class AtomizeConfigFailureE2ETests(unittest.TestCase):
    def _env(self, tmp: str) -> dict[str, str]:
        return {
            "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-config",
            "PSC_CONFIG_ROOT": f"{tmp}/cfg/.config/paulshaclaw",
            "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            "PSC_MEMORY_ROOT": f"{tmp}/memory",
        }

    def _write_malformed_config(self, tmp: str) -> None:
        cfg_dir = Path(tmp) / "hippo-config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(
            _MALFORMED_CONFIG, encoding="utf-8"
        )

    def _seed_split_session(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        processing.append_state(
            root, session_key="claude:s1", state="split",
            now="2026-07-09T00:00:00Z", config_hash="h", fragments=2,
        )

    def _run_atomize(self, tmp: str, root: Path, *extra: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self._env(tmp)), redirect_stdout(buf):
            rc = cli.main(["atomize", "--memory-root", str(root),
                           "--now", "2026-07-10T00:00:00Z", *extra])
        return rc, json.loads(buf.getvalue())

    def test_malformed_canonical_config_parks_split_sessions_and_exits_nonzero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            self._write_malformed_config(tmp)
            self._seed_split_session(root)

            rc, payload = self._run_atomize(tmp, root)

            # 結構化錯誤收斂：exit 非零、不 traceback 逃逸
            self.assertEqual(rc, 1)
            self.assertEqual(payload["error"], "AtomizerConfigError")
            self.assertIn("external_agents.profiles", payload["error_message"])
            self.assertEqual(payload["failure_category"], "backend_unavailable")
            self.assertEqual(payload["parked"], ["claude:s1"])

            # spec「config 無效立即 parked」：failure category＋證據齊備
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            event = processing.fold_events(root)["claude:s1"]
            self.assertEqual(event["failure_category"], "backend_unavailable")
            self.assertIn("external_agents.profiles", event["error"])
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            self.assertTrue(evidence.exists())
            evidence_payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(
                evidence_payload["failure_category"], "backend_unavailable"
            )

    def test_missing_canonical_config_parks_split_sessions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            self._seed_split_session(root)

            rc, payload = self._run_atomize(tmp, root, "--promoter", "llm")

            self.assertEqual(rc, 1)
            self.assertEqual(payload["error"], "AtomizerConfigError")
            self.assertIn("Canonical runtime config not found", payload["error_message"])
            self.assertEqual(payload["failure_category"], "backend_unavailable")
            self.assertEqual(payload["parked"], ["claude:s1"])
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            event = processing.fold_events(root)["claude:s1"]
            self.assertEqual(event["failure_category"], "backend_unavailable")
            self.assertTrue(
                (root / "runtime" / "queue" / "_failed" / "claude__s1.json").exists()
            )

    def test_dry_run_init_failure_is_mutation_free(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            self._write_malformed_config(tmp)
            self._seed_split_session(root)

            rc, payload = self._run_atomize(tmp, root, "--dry-run")

            # 失敗仍顯性（exit 1），但 dry-run 不得 park、不得落證據
            self.assertEqual(rc, 1)
            self.assertEqual(payload["parked"], [])
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertFalse(
                (root / "runtime" / "queue" / "_failed" / "claude__s1.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
