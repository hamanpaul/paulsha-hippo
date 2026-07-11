"""#15 E2E：backend 故障 → park（含證據）→ 修復 backend → requeue → 成功 promote。

spec §3.1「E2E 必測」的完整循環驗收。
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli
from paulsha_hippo.atomizer import agent_exec, llm_promoter, pipeline
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.ledger import processing

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-07-10T00:00:00Z"
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

_VALID_ONE_SLICE = (
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],'
    '"body":"body a","source_fragment_indices":[0,1],"relations":[]}]'
)


def _seed_raw(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-07-10" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW, encoding="utf-8")
    return raw


class UnavailableThenFixedClient(agent_exec.AgentClient):
    """模擬 backend 先壞後修：前 fail_times 次 raise AgentUnavailableError，之後回有效輸出。"""

    def __init__(self, fail_times: int, output: str) -> None:
        self._fail_times = fail_times
        self._output = output
        self.calls = 0

    def run(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise agent_exec.AgentUnavailableError("agent command not found: claude")
        return self._output


class ParkRequeuePromoteE2ETests(unittest.TestCase):
    def test_backend_failure_park_fix_requeue_promote_cycle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            client = UnavailableThenFixedClient(1, _VALID_ONE_SLICE)
            cached = agent_exec.CachingAgentClient(
                client, root / "runtime" / "cache" / "atomize"
            )
            promoter = llm_promoter.LLMPromoter(
                cached, skill_text="E2E-SKILL",
                known_projects=["paulshaclaw"], model="fake-llm",
            )

            # 1) backend 故障 → park（含證據；backend_unavailable 不重試）
            pipeline.run(root, config=cfg, config_hash=h,
                         now="2026-07-10T01:00:00Z", promoter=promoter)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            parked_event = processing.read_events(root)[-1]
            self.assertEqual(parked_event["failure_category"], "backend_unavailable")
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            self.assertTrue(evidence.exists())
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["session_key"], "claude:s1")
            self.assertEqual(payload["failure_category"], "backend_unavailable")
            # split fragments 保留、cache/sidecar 已清
            self.assertEqual(len(list((root / "inbox" / "_slices").rglob("*.md"))), 2)
            cache_dir = root / "runtime" / "cache" / "atomize"
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertEqual(list(cache_dir.glob("*.retries")), [])

            # 2) parked 不佔下一輪 atomize 預算（backend 未修也不再呼叫）
            calls_before = client.calls
            pipeline.run(root, config=cfg, config_hash=h,
                         now="2026-07-10T02:00:00Z", promoter=promoter)
            self.assertEqual(client.calls, calls_before)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")

            # 3) backend 已修復（client 下次成功）→ hippo requeue
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["requeue", "claude:s1", "--memory-root", str(root),
                               "--now", "2026-07-10T03:00:00Z",
                               "--reason", "backend fixed"])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual(summary["requeued"][0]["session_key"], "claude:s1")
            self.assertEqual(summary["requeued"][0]["fragments"], 2)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            requeue_event = processing.read_events(root)[-1]
            self.assertEqual(requeue_event["requeued_from"], "parked")
            self.assertEqual(requeue_event["requeue_reason"], "backend fixed")

            # 4) 重走 promote → promoted、knowledge 落地、快取全清
            result = pipeline.run(root, config=cfg, config_hash=h,
                                  now="2026-07-10T04:00:00Z", promoter=promoter)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(len(list((root / "knowledge").rglob("*.md"))), 1)
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            # 證據檔保留為歷史紀錄
            self.assertTrue(evidence.exists())


if __name__ == "__main__":
    unittest.main()
