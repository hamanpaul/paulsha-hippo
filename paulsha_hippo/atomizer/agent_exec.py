from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path

from ..agent_profiles import (
    AgentProfile,
    AgentRunResult,
    child_environment,
    classify_failure,
    sanitize_stderr,
)


class AgentExecError(Exception):
    """Raised when an agent subprocess cannot produce usable output."""

    def __init__(self, message: str, *, category: str = "process", stderr: str = "", exit_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.stderr = stderr
        self.exit_code = exit_code


class AgentUnavailableError(AgentExecError):
    """backend executable 不存在／未設定（#15 分類：backend_unavailable，不重試）。"""


class AgentTransientError(AgentExecError):
    """timeout／non-zero exit／空輸出／端點不可達（#15 分類：transient，有限重試）。"""


class AgentClient(ABC):
    @abstractmethod
    def run(self, prompt: str) -> str:
        """Return raw agent output for a prompt."""


class AgentExecClient(AgentClient):
    def __init__(
        self,
        command: list[str],
        timeout: int = 300,
        env: dict | None = None,
        *,
        profile: AgentProfile | None = None,
    ) -> None:
        self._command = list(command)
        self._timeout = timeout
        self._env = dict(env) if env is not None else None
        self._profile = profile
        self.last_result = None

    def run_with_evidence(self, prompt: str) -> tuple[str, str, int | None]:
        """執行一個 external CLI，回傳 stdout + sanitized stderr + rc。

        ``env`` 不再從 parent 合併；只有固定 allowlist 的 non-secret values 可以
        進 child process。Prompt 永遠由 stdin 傳遞。
        """
        if not self._command:
            raise AgentUnavailableError("agent command not configured")
        try:
            env = child_environment(self._env)
        except Exception as exc:
            raise AgentUnavailableError(str(exc)) from exc
        try:
            # An empty working directory prevents checkout-local AGENTS/CLAUDE
            # files from becoming implicit input or a tool target.
            with tempfile.TemporaryDirectory(prefix="hippo-agent-") as cwd:
                completed = subprocess.run(
                    self._command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                    shell=False,
                    env=env,
                    cwd=cwd,
                )
        except FileNotFoundError as exc:
            raise AgentUnavailableError(
                f"agent command not found: {self._command[0]}"
            ) from exc
        except PermissionError as exc:
            raise AgentUnavailableError(
                f"agent command not executable: {self._command[0]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentTransientError(
                f"agent timed out after {self._timeout}s",
                category="timeout",
                stderr=sanitize_stderr(getattr(exc, "stderr", "")),
            ) from exc
        stderr = sanitize_stderr(completed.stderr)
        if completed.returncode != 0:
            raise AgentTransientError(
                f"agent exited with code {completed.returncode}",
                category=classify_failure(
                    stderr, exit_code=completed.returncode
                ),
                stderr=stderr,
                exit_code=completed.returncode,
            )
        if not completed.stdout.strip():
            raise AgentTransientError(
                "agent produced empty output",
                category="empty_output",
                stderr=stderr,
                exit_code=completed.returncode,
            )
        self.last_result = {
            "stderr": stderr,
            "exit_code": completed.returncode,
            "command": tuple(self._command),
        }
        return completed.stdout, stderr, completed.returncode

    def run(self, prompt: str) -> str:
        output, _stderr, _returncode = self.run_with_evidence(prompt)
        return output


class FakeAgentClient(AgentClient):
    def __init__(self, canned_output: str) -> None:
        self._canned_output = canned_output

    def run(self, prompt: str) -> str:
        return self._canned_output


class CachingAgentClient(AgentClient):
    """Freeze raw output so reruns stay deterministic."""

    def __init__(self, inner: AgentClient, cache_dir: Path) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self.last_result: AgentRunResult | None = None

    def cache_path_for_key(self, cache_key: str) -> Path:
        return self._cache_dir / f"{cache_key}.json"

    def cache_path_for(self, prompt: str) -> Path:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return self.cache_path_for_key(prompt_hash)

    @staticmethod
    def _write_text_atomically(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def run_cached(self, prompt: str, cache_key: str) -> str:
        path = self.cache_path_for_key(cache_key)
        self.last_result = None
        if path.exists():
            try:
                cached = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                pass
            else:
                if cached:
                    # Router-backed entries carry the selected profile and the
                    # complete bounded attempt chain.  Replaying that envelope
                    # preserves honest provenance without storing the prompt.
                    try:
                        envelope = json.loads(cached)
                    except json.JSONDecodeError:
                        envelope = None
                    if isinstance(envelope, dict) and envelope.get("cache_schema") == "2":
                        output = envelope.get("output")
                        attempts_raw = envelope.get("attempts", [])
                        try:
                            attempts = tuple(
                                AgentRunResult(**item)
                                for item in attempts_raw
                                if isinstance(item, dict)
                            )
                            result_raw = envelope.get("provenance")
                            result = AgentRunResult(**result_raw) if isinstance(result_raw, dict) else None
                        except (TypeError, ValueError):
                            attempts = ()
                            result = None
                        if isinstance(output, str) and result is not None:
                            self.last_result = result
                            if hasattr(self._inner, "last_result"):
                                self._inner.last_result = result
                            if hasattr(self._inner, "attempts"):
                                self._inner.attempts = attempts or (result,)
                            return output
                        # A recognized router envelope that fails validation is
                        # treated as cache corruption, not as a raw answer.
                    else:
                        # Legacy/fake clients intentionally store raw text.
                        return cached
        output = self._inner.run(prompt)
        result = getattr(self._inner, "last_result", None)
        if isinstance(result, AgentRunResult):
            attempts = getattr(self._inner, "attempts", ())
            payload = {
                "cache_schema": "2",
                "output": output,
                "provenance": asdict(result),
                "attempts": [asdict(item) for item in attempts if isinstance(item, AgentRunResult)],
            }
            self._write_text_atomically(
                path,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
            self.last_result = result
        else:
            self._write_text_atomically(path, output)
        return output

    def run(self, prompt: str) -> str:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return self.run_cached(prompt, prompt_hash)

    def cache_namespace(self) -> str:
        inner = self._inner
        provider = getattr(inner, "cache_namespace", None)
        if callable(provider):
            return str(provider())
        return ""

    def clear_cache_key(self, cache_key: str) -> None:
        try:
            self.cache_path_for_key(cache_key).unlink()
        except FileNotFoundError:
            return
