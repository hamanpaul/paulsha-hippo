from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.skillopt import optimizer_acp


class _RecordingStdin:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, data: str) -> int:
        self.parts.append(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def lines(self) -> list[dict[str, object]]:
        return [json.loads(line) for chunk in self.parts for line in chunk.splitlines() if line.strip()]


class _FakeProcess:
    def __init__(self, stdout_text: str) -> None:
        self.stdin = _RecordingStdin()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO()

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        return None


class OptimizerAcpRunnerTests(unittest.TestCase):
    def test_default_runner_uses_isolated_temp_cwd_and_completes_prompt(self) -> None:
        proc = _FakeProcess(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "sess_123"}}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": "sess_123",
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": "edited skill"},
                                },
                            },
                        }
                    ),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}),
                    "",
                ]
            )
        )

        with mock.patch("paulsha_hippo.skillopt.optimizer_acp.subprocess.Popen", return_value=proc):
            output = optimizer_acp._default_runner("optimize this skill")

        self.assertEqual(output, "edited skill")
        sent = proc.stdin.lines()
        self.assertEqual(sent[0]["method"], "initialize")
        self.assertEqual(sent[1]["method"], "session/new")
        isolated_cwd = Path(str(sent[1]["params"]["cwd"]))
        repo_root = Path(__file__).resolve().parents[1]
        self.assertNotEqual(isolated_cwd, repo_root)
        self.assertTrue(str(isolated_cwd).startswith(tempfile.gettempdir()))
        self.assertEqual(sent[2]["method"], "session/prompt")


if __name__ == "__main__":
    unittest.main()
