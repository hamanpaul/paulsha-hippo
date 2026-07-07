from __future__ import annotations

import json
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

_ADAPTER = Path(__file__).resolve().parent / "codex_exec_acp_adapter.py"
_REPO_ROOT = Path(__file__).resolve().parents[2]


_PROMPT_TEMPLATE = """You are optimizing a reusable agent SKILL.md (a trainable instruction document).

Current skill:
<<<SKILL
{skill}
SKILL

A model following this skill scored low on these cases (input / expected / got / score):
{failures}

Propose exactly ONE bounded edit (add, replace, or delete a small section) that should
improve performance on such cases. Keep the skill's structure and frontmatter intact.
Return ONLY the complete edited SKILL.md, with no commentary and no code fences.
"""


def _format_failures(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return "(none)"

    lines: list[str] = []
    for f in failures:
        lines.append(
            "- input: {inp!r}\n  expected: {gold!r}\n  got: {out!r}\n  score: {score}".format(
                inp=f.get("input"),
                gold=f.get("gold"),
                out=f.get("output"),
                score=f.get("score"),
            )
        )
    return "\n".join(lines)


def _normalize_skill_text(text: str) -> str:
    # Normalize only trailing newlines; do not strip other whitespace.
    return text.rstrip("\n") + "\n"


def _extract_text_from_codex_exec_output(raw: str) -> str:
    """Extract assistant text from codex exec output.

    The codex ACP adapter returns the content of the file written by
    `codex exec --output-last-message`. That may be plain text or JSON.
    """

    s = raw.strip()
    if not s:
        return ""

    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return raw

    def blocks_to_text(blocks: Any) -> str:
        if isinstance(blocks, str):
            return blocks
        if isinstance(blocks, list):
            parts: list[str] = []
            for b in blocks:
                if isinstance(b, str):
                    parts.append(b)
                elif isinstance(b, dict):
                    t = b.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            return "\n".join(parts)
        if isinstance(blocks, dict):
            t = blocks.get("text")
            return t if isinstance(t, str) else ""
        return ""

    if isinstance(obj, dict):
        # Common shapes observed in chat/message dumps.
        if "content" in obj:
            return blocks_to_text(obj.get("content"))
        if "message" in obj and isinstance(obj["message"], dict):
            msg = obj["message"]
            if "content" in msg:
                return blocks_to_text(msg.get("content"))
            if "text" in msg and isinstance(msg["text"], str):
                return msg["text"]
        if "text" in obj and isinstance(obj["text"], str):
            return obj["text"]

    # Unknown JSON shape: return as-is.
    return raw


class _TimedLineReader:
    def __init__(self, stream: Any, now: Callable[[], float]):
        self._stream = stream
        self._now = now
        self._selector: selectors.BaseSelector | None = None

        try:
            # Only works for real file objects (TextIOWrapper from subprocess).
            stream.fileno()
        except Exception:
            return

        sel = selectors.DefaultSelector()
        sel.register(stream, selectors.EVENT_READ)
        self._selector = sel

    def readline(self, deadline: float) -> str:
        remaining = deadline - self._now()
        if remaining <= 0:
            raise TimeoutError("readline")

        if self._selector is None:
            return self._stream.readline()

        events = self._selector.select(timeout=remaining)
        if not events:
            raise TimeoutError("readline")
        return self._stream.readline()

    def close(self) -> None:
        if self._selector is None:
            return
        try:
            self._selector.unregister(self._stream)
        except Exception:
            pass
        self._selector.close()
        self._selector = None


def _default_runner(
    prompt: str,
    *,
    session_new_timeout_s: float = 30.0,
    prompt_timeout_s: float = 300.0,
    _time_monotonic: Callable[[], float] = time.monotonic,
    _line_reader_factory: Callable[[Any, Callable[[], float]], Any] | None = None,
) -> str:
    """Run a single prompt through the codex ACP JSON-RPC adapter.

    The adapter speaks ACP-style JSON-RPC over stdin/stdout (initialize,
    session/new, session/prompt). We drive just enough protocol to get the
    assistant's text output.

    Timeouts are bounded (fail-closed) so we never block indefinitely waiting for:
    - session/new to return sessionId
    - the final prompt completion response (id=3)
    """

    if not _ADAPTER.exists():
        raise FileNotFoundError(f"missing codex ACP adapter script: {_ADAPTER}")

    with tempfile.TemporaryDirectory(prefix="skillopt-optimizer-") as sandbox_cwd:
        proc = subprocess.Popen(
            [sys.executable, str(_ADAPTER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None

        def write(msg: dict[str, Any]) -> None:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        make_reader = _line_reader_factory or (lambda stream, now: _TimedLineReader(stream, now))
        reader = make_reader(proc.stdout, _time_monotonic)

        session_id: str | None = None
        chunks: list[str] = []

        try:
            write({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            write({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                # Isolate optimizer side effects from the repo; only accepted skills are written later.
                "params": {"cwd": sandbox_cwd},
            })

            deadline = _time_monotonic() + session_new_timeout_s
            while True:
                try:
                    raw = reader.readline(deadline)
                except TimeoutError:
                    raise RuntimeError("timed out waiting for session/new sessionId") from None

                if raw == "":
                    break

                line = raw.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("id") == 2 and isinstance(msg.get("result"), dict):
                    sid = msg["result"].get("sessionId")
                    if isinstance(sid, str) and sid:
                        session_id = sid
                        break

            if not session_id:
                raise RuntimeError("codex ACP adapter did not return sessionId")

            write(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                }
            )

            prompt_completed = False
            deadline = _time_monotonic() + prompt_timeout_s
            while True:
                try:
                    raw = reader.readline(deadline)
                except TimeoutError:
                    raise RuntimeError(
                        "timed out waiting for prompt completion (id=3)"
                    ) from None

                if raw == "":
                    break

                line = raw.strip()
                if not line:
                    continue
                msg = json.loads(line)

                if msg.get("method") == "session/update":
                    params = msg.get("params") or {}
                    if params.get("sessionId") != session_id:
                        continue

                    # Adapter emits params['update'] directly; keep a fallback for older nested shapes.
                    update = params.get("update") or {}
                    if isinstance(update, dict) and "update" in update and "content" not in update:
                        update = update.get("update") or {}

                    content = update.get("content") if isinstance(update, dict) else None
                    if isinstance(content, dict) and content.get("type") == "text":
                        text = content.get("text")
                        if isinstance(text, str):
                            chunks.append(text)
                    continue

                if msg.get("id") == 3:
                    if "error" in msg:
                        err = msg.get("error")
                        if isinstance(err, dict):
                            code = err.get("code")
                            message = err.get("message")
                            details = (
                                f"{code}: {message}"
                                if code is not None and message
                                else (message or str(err))
                            )
                        else:
                            details = str(err)
                        raise RuntimeError(f"codex ACP prompt failed: {details}")
                    prompt_completed = True
                    break

            if not prompt_completed:
                raise RuntimeError(
                    "codex ACP adapter terminated before returning prompt completion (id=3)"
                )

            output = "".join(chunks)
            return _extract_text_from_codex_exec_output(output)

        finally:
            try:
                reader.close()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def make_acp_optimizer(
    *,
    runner: Callable[[str], str] = _default_runner,
) -> Callable[[str, list[dict[str, Any]]], str]:
    """Create a SkillOpt optimizer backed by a codex ACP runner."""

    def optimizer(skill_text: str, failures: list[dict[str, Any]]) -> str:
        prompt = _PROMPT_TEMPLATE.format(skill=skill_text, failures=_format_failures(failures))
        edited = runner(prompt)
        return _normalize_skill_text(edited)

    return optimizer
