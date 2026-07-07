#!/usr/bin/env python3
"""Minimal ACP adapter that bridges session/prompt to codex exec."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


def _write_jsonrpc(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error_response(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _config_options(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": session["model"],
            "options": [
                {"value": session["model"], "name": session["model"]},
                {"value": "gpt-5.3-codex", "name": "gpt-5.3-codex"},
                {"value": "gpt-5-mini", "name": "gpt-5-mini"},
            ],
        },
        {
            "id": "thought_level",
            "name": "Thought Level",
            "category": "thought_level",
            "type": "select",
            "currentValue": session["reasoning"],
            "options": [
                {"value": session["reasoning"], "name": session["reasoning"]},
                {"value": "low", "name": "low"},
                {"value": "medium", "name": "medium"},
                {"value": "high", "name": "high"},
                {"value": "xhigh", "name": "xhigh"},
            ],
        },
    ]


def _collect_prompt_text(prompt_blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in prompt_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n\n".join(parts).strip()


def _run_codex_exec(
    *,
    codex_bin: str,
    cwd: Path,
    model: str,
    reasoning: str,
    sandbox: str,
    prompt: str,
    timeout_sec: int,
    skip_git_repo_check: bool,
) -> str:
    with tempfile.TemporaryDirectory(prefix="codex-acp-adapter-") as tds:
        td = Path(tds)
        output_path = td / "assistant-output.json"
        cmd = [
            codex_bin,
            "exec",
            "-C",
            str(cwd),
            "-c",
            f'model="{model}"',
            "-c",
            f'model_reasoning_effort="{reasoning}"',
            "--sandbox",
            sandbox,
            "--output-last-message",
            str(output_path),
            prompt,
        ]
        if skip_git_repo_check:
            cmd.insert(len(cmd) - 1, "--skip-git-repo-check")
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=max(5, timeout_sec),
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or "codex exec failed"
            raise RuntimeError(err)
        if not output_path.exists():
            raise RuntimeError("codex exec output file not found")
        return output_path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--default-model", default="gpt-5.3-codex")
    parser.add_argument("--default-reasoning", default="xhigh")
    parser.add_argument("--sandbox", default="workspace-write")
    parser.add_argument("--timeout-sec", type=int, default=240)
    parser.add_argument(
        "--skip-git-repo-check",
        dest="skip_git_repo_check",
        action="store_true",
        default=True,
        help="Pass --skip-git-repo-check to codex exec (default: enabled).",
    )
    parser.add_argument(
        "--no-skip-git-repo-check",
        dest="skip_git_repo_check",
        action="store_false",
        help="Disable --skip-git-repo-check passthrough.",
    )
    args = parser.parse_args()

    sessions: dict[str, dict[str, Any]] = {}
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {
                            "loadSession": False,
                            "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                            "mcp": {"http": False, "sse": False},
                        },
                        "agentInfo": {
                            "name": "codex-exec-acp-adapter",
                            "title": "Codex Exec ACP Adapter",
                            "version": "0.1.0",
                        },
                        "authMethods": [],
                    },
                }
            )
            continue

        if method == "session/new":
            cwd = str(params.get("cwd", "")).strip()
            if not cwd:
                _write_jsonrpc(_error_response(rid, -32602, "missing cwd"))
                continue
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            sessions[session_id] = {
                "cwd": cwd,
                "model": args.default_model,
                "reasoning": args.default_reasoning,
            }
            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "sessionId": session_id,
                        "configOptions": _config_options(sessions[session_id]),
                    },
                }
            )
            continue

        if method == "session/set_config_option":
            session_id = str(params.get("sessionId", "")).strip()
            session = sessions.get(session_id)
            if not session:
                _write_jsonrpc(_error_response(rid, -32602, "invalid sessionId"))
                continue
            config_id = str(params.get("configId", "")).strip()
            value = str(params.get("value", "")).strip()
            if config_id == "model" and value:
                session["model"] = value
            elif config_id in {"thought_level", "reasoning", "reasoning_effort"} and value:
                session["reasoning"] = value
            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"configOptions": _config_options(session)},
                }
            )
            continue

        if method == "session/prompt":
            session_id = str(params.get("sessionId", "")).strip()
            session = sessions.get(session_id)
            if not session:
                _write_jsonrpc(_error_response(rid, -32602, "invalid sessionId"))
                continue
            prompt_blocks = params.get("prompt")
            if not isinstance(prompt_blocks, list):
                _write_jsonrpc(_error_response(rid, -32602, "invalid prompt"))
                continue
            prompt_text = _collect_prompt_text(prompt_blocks)
            if not prompt_text:
                _write_jsonrpc(_error_response(rid, -32602, "empty prompt text"))
                continue
            try:
                assistant_output = _run_codex_exec(
                    codex_bin=args.codex_bin,
                    cwd=Path(str(session["cwd"])),
                    model=str(session["model"]),
                    reasoning=str(session["reasoning"]),
                    sandbox=args.sandbox,
                    prompt=prompt_text,
                    timeout_sec=max(5, args.timeout_sec),
                    skip_git_repo_check=bool(args.skip_git_repo_check),
                )
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                _write_jsonrpc(_error_response(rid, -32000, f"codex exec bridge failed: {exc}"))
                continue

            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": assistant_output,
                            },
                        },
                    },
                }
            )
            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"stopReason": "end_turn"},
                }
            )
            continue

        if method == "session/cancel":
            continue

        if rid is None:
            continue
        _write_jsonrpc(_error_response(rid, -32601, f"unsupported method: {method}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
