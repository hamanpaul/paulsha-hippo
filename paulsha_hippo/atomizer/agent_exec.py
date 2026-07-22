from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Sequence

from ..agent_profiles import (
    AgentProfile,
    AgentRunResult,
    ExternalAgentRouter,
    RESPONSE_SCHEMA_VERSION,
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
        self.last_cache_keys: tuple[str, ...] = ()

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

    def _inner_response_schema(self) -> str | None:
        value = getattr(self._inner, "response_schema", None)
        return str(value) if value else None

    def _requires_envelope(
        self,
        *,
        response_schema: str | None,
        response_validator: Callable[[str], object] | None,
    ) -> bool:
        return bool(
            response_schema
            or response_validator
            or callable(getattr(self._inner, "cache_namespace", None))
        )

    @staticmethod
    def _profile_cache_key(
        base_key: str,
        profile_id: str,
        response_schema: str,
    ) -> str:
        identity = json.dumps(
            {
                "base_key": base_key,
                "profile_id": profile_id,
                "response_schema": response_schema,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        prefix = base_key.rsplit("__", 1)[0]
        return f"{prefix}__{digest}"

    def _read_envelope(
        self,
        path: Path,
        *,
        response_schema: str | None,
        response_validator: Callable[[str], object] | None,
    ) -> tuple[str, AgentRunResult, tuple[AgentRunResult, ...]] | None:
        try:
            cached = path.read_text(encoding="utf-8")
            envelope = json.loads(cached)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict) or envelope.get("cache_schema") != "2":
            return None
        if response_schema is not None and envelope.get("response_schema") != response_schema:
            return None
        output = envelope.get("output")
        result_raw = envelope.get("provenance")
        attempts_raw = envelope.get("attempts", [])
        if not isinstance(output, str) or not isinstance(result_raw, dict):
            return None
        if not isinstance(attempts_raw, list) or len(attempts_raw) > 6:
            return None
        try:
            result = AgentRunResult(**result_raw)
            attempts = tuple(
                AgentRunResult(**item)
                for item in attempts_raw
                if isinstance(item, dict)
            )
        except (TypeError, ValueError):
            return None
        if len(attempts) != len(attempts_raw):
            return None
        if response_schema is not None and result.response_schema != response_schema:
            return None
        if response_validator is not None:
            try:
                response_validator(output)
            except Exception:
                return None
        return output, result, attempts

    def _set_inner_result(
        self,
        result: AgentRunResult,
        attempts: Sequence[AgentRunResult],
    ) -> None:
        self.last_result = result
        if hasattr(self._inner, "last_result"):
            self._inner.last_result = result
        if hasattr(self._inner, "attempts"):
            self._inner.attempts = tuple(attempts) or (result,)

    def _call_inner(
        self,
        prompt: str,
        response_validator: Callable[[str], object] | None,
    ) -> str:
        if isinstance(self._inner, ExternalAgentRouter):
            return self._inner.run(prompt, response_validator=response_validator)
        return self._inner.run(prompt)

    def run_cached(
        self,
        prompt: str,
        cache_key: str,
        *,
        response_validator: Callable[[str], object] | None = None,
        response_schema: str | None = None,
    ) -> str:
        path = self.cache_path_for_key(cache_key)
        self.last_result = None
        self.last_cache_keys = (cache_key,)
        expected_schema = response_schema or self._inner_response_schema()
        requires_envelope = self._requires_envelope(
            response_schema=expected_schema,
            response_validator=response_validator,
        )
        if path.exists():
            if requires_envelope:
                envelope = self._read_envelope(
                    path,
                    response_schema=expected_schema,
                    response_validator=response_validator,
                )
                if envelope is not None:
                    output, result, attempts = envelope
                    self._set_inner_result(result, attempts)
                    return output
                # Typed caches reject legacy raw text and malformed envelopes;
                # they are a miss and must go through the bounded agent path.
            else:
                try:
                    cached = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    cached = ""
                if cached:
                    return cached

        output = self._call_inner(prompt, response_validator)
        result = getattr(self._inner, "last_result", None)
        if isinstance(result, AgentRunResult):
            attempts = getattr(self._inner, "attempts", ())
            payload = {
                "cache_schema": "2",
                "response_schema": expected_schema or result.response_schema,
                "output": output,
                "provenance": asdict(result),
                "attempts": [asdict(item) for item in attempts if isinstance(item, AgentRunResult)],
            }
            self._write_text_atomically(
                path,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
            self.last_result = result
        elif not requires_envelope:
            self._write_text_atomically(path, output)
        return output

    def run_session(
        self,
        prompts: Sequence[str],
        *,
        cache_keys: Sequence[str] | None = None,
        response_validator: Callable[[str], object] | None = None,
        response_schema: str | None = None,
    ) -> tuple[str, ...]:
        """Run/cache a complete router session without persisting partial chunks."""
        frozen_prompts = tuple(str(prompt) for prompt in prompts)
        if not frozen_prompts:
            self.last_cache_keys = ()
            return ()
        if cache_keys is None:
            base_keys = tuple(
                hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                for prompt in frozen_prompts
            )
        else:
            base_keys = tuple(cache_keys)
            if len(base_keys) != len(frozen_prompts):
                raise ValueError("cache_keys must match the frozen prompt session")
        expected_schema = response_schema or self._inner_response_schema() or RESPONSE_SCHEMA_VERSION
        if not isinstance(self._inner, ExternalAgentRouter):
            outputs = tuple(
                self.run_cached(
                    prompt,
                    key,
                    response_validator=response_validator,
                    response_schema=response_schema,
                )
                for prompt, key in zip(frozen_prompts, base_keys)
            )
            self.last_cache_keys = base_keys
            return outputs

        # A complete cache hit is considered only for one profile.  Partial or
        # schema-invalid entries are removed from that profile's candidate set;
        # they must never be combined with another profile's chunks.
        for profile in self._inner.profiles:
            eligible, _reason = profile.eligible(
                task_class=self._inner.task_class,
                path=self._inner.execution_path,
            )
            if not eligible:
                continue
            profile_keys = tuple(
                self._profile_cache_key(key, profile.id, expected_schema)
                for key in base_keys
            )
            cached_entries = [
                self._read_envelope(
                    self.cache_path_for_key(key),
                    response_schema=expected_schema,
                    response_validator=response_validator,
                )
                for key in profile_keys
            ]
            if all(entry is not None for entry in cached_entries):
                first = cached_entries[0]
                assert first is not None
                if all(entry[1].profile_id == profile.id for entry in cached_entries if entry is not None):
                    outputs = tuple(entry[0] for entry in cached_entries if entry is not None)
                    self._set_inner_result(first[1], first[2])
                    self.last_cache_keys = profile_keys
                    return outputs
            for key in profile_keys:
                self.clear_cache_key(key)

        outputs = tuple(
            self._inner.run_session(
                frozen_prompts,
                response_validator=response_validator,
            )
        )
        result = self._inner.last_result
        if not isinstance(result, AgentRunResult):
            self.last_cache_keys = base_keys
            return outputs
        profile_keys = tuple(
            self._profile_cache_key(key, result.profile_id, expected_schema)
            for key in base_keys
        )
        attempts = getattr(self._inner, "attempts", ())
        payloads = [
            {
                "cache_schema": "2",
                "response_schema": expected_schema,
                "output": output,
                "provenance": asdict(result),
                "attempts": [
                    asdict(item)
                    for item in attempts
                    if isinstance(item, AgentRunResult)
                ],
            }
            for output in outputs
        ]
        for key, payload in zip(profile_keys, payloads):
            self._write_text_atomically(
                self.cache_path_for_key(key),
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        self.last_result = result
        self.last_cache_keys = profile_keys
        return outputs

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
