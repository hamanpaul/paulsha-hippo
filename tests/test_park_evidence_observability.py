"""issue #69 子項 A：external agent 呼叫的可觀測性。

parked evidence 過去只有 attempts/cache_key/error/failure_category/
last_output_bytes/last_output_sha256——timeout、CLI 靜默失敗、rate limit、
進程被殺完全無法區分（實例：claude 對真實 payload 吐 0 bytes，evidence 無
stderr、無 exit code 可查）。這裡新增 attempts_detail，逐一以 fake agent
shell script（真實 subprocess，走 ExternalAgentRouter → AgentExecClient）
模擬 empty_output／nonzero_exit／timeout 三種失敗形狀，驗證 park evidence
JSON 含每次嘗試的 profile_id/exit_code/duration_seconds/stderr_tail/
stdout_bytes/failure_kind，且 stderr_tail 已去敏截斷。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.agent_profiles import AgentProfile, ExternalAgentRouter
from paulsha_hippo.atomizer import agent_exec, config as atomizer_config, llm_promoter, pipeline
from paulsha_hippo.ledger import processing

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-07-27T00:00:00Z"
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
    raw = root / "inbox" / "research" / "claude" / "2026-07-27" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW, encoding="utf-8")
    return raw


def _write_fake_agent(tmp: Path, name: str, script: str) -> Path:
    path = tmp / name
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def _profile_for_script(script_path: Path, *, timeout: int = 30) -> AgentProfile:
    return AgentProfile(
        id="fakeagent",
        tier=1,
        priority=1,
        traits=("test",),
        task_classes=("atomization",),
        model="test-model",
        effort="medium",
        supported_efforts=("medium",),
        argv=(str(script_path),),
        supported_models=("test-model",),
        timeout=timeout,
    )


class ParkEvidenceAttemptsDetailTests(unittest.TestCase):
    def _run_once(self, tmp: Path, script_path: Path, *, timeout: int = 30):
        root = tmp / "memory"
        _seed_raw(root)
        cfg, config_hash = atomizer_config.load_config(override_path=None)
        profile = _profile_for_script(script_path, timeout=timeout)
        router = ExternalAgentRouter((profile,))
        cached = agent_exec.CachingAgentClient(
            router, root / "runtime" / "cache" / "atomize"
        )
        promoter = llm_promoter.LLMPromoter(
            cached, skill_text="OBS-SKILL",
            known_projects=["paulshaclaw"], model="fake-llm",
        )
        pipeline.run(
            root, config=cfg, config_hash=config_hash,
            now="2026-07-27T01:00:00Z", promoter=promoter,
        )
        self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
        evidence_path = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
        self.assertTrue(evidence_path.exists())
        return json.loads(evidence_path.read_text(encoding="utf-8"))

    def test_empty_output_failure_kind(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script = _write_fake_agent(
                tmp_path,
                "fake-empty.sh",
                "#!/bin/sh\ncat >/dev/null\n"
                "echo 'boom: no output produced' >&2\n"
                "exit 0\n",
            )
            payload = self._run_once(tmp_path, script)

        # 既有欄位一個都不動
        self.assertEqual(payload["session_key"], "claude:s1")
        self.assertIn("last_output_bytes", payload)
        self.assertIn("last_output_sha256", payload)

        detail = payload["attempts_detail"]
        self.assertEqual(len(detail), 1)
        attempt = detail[0]
        self.assertEqual(attempt["profile_id"], "fakeagent")
        self.assertEqual(attempt["failure_kind"], "empty_output")
        self.assertEqual(attempt["exit_code"], 0)
        self.assertEqual(attempt["stdout_bytes"], 0)
        self.assertIn("boom: no output produced", attempt["stderr_tail"])
        self.assertLessEqual(len(attempt["stderr_tail"]), 2000)

    def test_nonzero_exit_failure_kind_and_stderr_redaction(self):
        secret = "ghp_" + "A1b2C3d4" * 5
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script = _write_fake_agent(
                tmp_path,
                "fake-nonzero.sh",
                "#!/bin/sh\ncat >/dev/null\n"
                f"echo 'fatal: auth token {secret} rejected' >&2\n"
                "exit 2\n",
            )
            payload = self._run_once(tmp_path, script)

        detail = payload["attempts_detail"]
        self.assertEqual(len(detail), 1)
        attempt = detail[0]
        self.assertEqual(attempt["profile_id"], "fakeagent")
        self.assertEqual(attempt["failure_kind"], "nonzero_exit")
        self.assertEqual(attempt["exit_code"], 2)
        self.assertLessEqual(len(attempt["stderr_tail"]), 2000)
        # 既有 secret-redaction 淨化必須套用在 stderr_tail 上
        self.assertNotIn(secret, attempt["stderr_tail"])
        self.assertNotIn(secret, json.dumps(payload))

    def test_timeout_failure_kind(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script = _write_fake_agent(
                tmp_path,
                "fake-hang.sh",
                "#!/bin/sh\ncat >/dev/null\nsleep 30\n",
            )
            payload = self._run_once(tmp_path, script, timeout=1)

        detail = payload["attempts_detail"]
        self.assertEqual(len(detail), 1)
        attempt = detail[0]
        self.assertEqual(attempt["profile_id"], "fakeagent")
        self.assertEqual(attempt["failure_kind"], "timeout")
        self.assertLessEqual(len(attempt["stderr_tail"]), 2000)


if __name__ == "__main__":
    unittest.main()
