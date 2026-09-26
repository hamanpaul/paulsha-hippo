"""Subprocess protocol tests for ``hippo task-memory``."""

from __future__ import annotations

import io
import pytest

from paulsha_hippo import cli
from paulsha_hippo.task_memory_provider import (
    MAX_STDIN_BYTES,
    TaskMemoryProviderError,
    serialize_protocol_json,
)


def _streams(monkeypatch, body: bytes):
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdin", io.BytesIO(body))
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    return stdout, stderr


def test_provide_cli_emits_protocol_json_only(monkeypatch):
    body = b'{"schema_version":"1","task_id":"t"}'
    stdout, stderr = _streams(monkeypatch, body)
    seen = []

    def provide(self, request):
        seen.append((self.memory_root, request))
        return {"ok": "payload"}

    monkeypatch.setattr("paulsha_hippo.task_memory_provider.TaskMemoryProvider.provide", provide)

    result = cli.main(["task-memory", "provide", "--memory-root", "/tmp/hippo-memory"])

    assert result == 0
    assert stdout.getvalue() == '{"ok":"payload"}\n'
    assert stderr.getvalue() == ""
    assert seen[0][1] == {"schema_version": "1", "task_id": "t"}


def test_fetch_cli_emits_bounded_code_and_nonzero_exit_on_error(monkeypatch):
    stdout, stderr = _streams(monkeypatch, b'{"envelope":{},"manifest":{},"note_id":"x"}')

    def fetch(_self, _request):
        raise TaskMemoryProviderError("hash-mismatch")

    monkeypatch.setattr("paulsha_hippo.task_memory_provider.TaskMemoryProvider.fetch", fetch)

    result = cli.main(["task-memory", "fetch"])

    assert result == 13
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "hippo-task-memory: hash-mismatch\n"


def test_oversized_stdin_is_rejected_before_provider_call(monkeypatch):
    stdout, stderr = _streams(monkeypatch, b" " * (MAX_STDIN_BYTES + 1))

    def unexpected(_self, _request):
        pytest.fail("provider must not run for oversized input")

    monkeypatch.setattr("paulsha_hippo.task_memory_provider.TaskMemoryProvider.provide", unexpected)

    result = cli.main(["task-memory", "provide"])

    assert result == 17
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "hippo-task-memory: size-limit\n"


def test_oversized_stdout_response_is_rejected():
    with pytest.raises(TaskMemoryProviderError) as exc:
        serialize_protocol_json({"content": "x" * (512 * 1024)})

    assert exc.value.code == "size-limit"
    assert exc.value.exit_code != 0


def test_task_memory_help_lists_protocol_modes(capsys):
    assert cli.main(["task-memory", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "provide" in help_text
    assert "fetch" in help_text
    assert "stdin envelope" in help_text
    assert "取回 note" in help_text
