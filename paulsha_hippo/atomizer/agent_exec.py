from __future__ import annotations

import hashlib
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path


class AgentExecError(Exception):
    """Raised when an agent subprocess cannot produce usable output."""


class AgentClient(ABC):
    @abstractmethod
    def run(self, prompt: str) -> str:
        """Return raw agent output for a prompt."""


class AgentExecClient(AgentClient):
    def __init__(self, command: list[str], timeout: int = 600, env: dict | None = None) -> None:
        self._command = list(command)
        self._timeout = timeout
        self._env = dict(env) if env is not None else None

    def run(self, prompt: str) -> str:
        if not self._command:
            raise AgentExecError("agent command not configured")
        try:
            completed = subprocess.run(
                self._command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                # #7：注入自捕捉標記——蒸餾子程序（claude -p 等）的 agent session
                # 其 hooks 讀到即跳過 queue write，斷開遞迴自捕捉。
                env={**os.environ, "HIPPO_SELF_SESSION": "1", **(self._env or {})},
            )
        except FileNotFoundError as exc:
            raise AgentExecError(f"agent command not found: {self._command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentExecError(f"agent timed out after {self._timeout}s") from exc
        if completed.returncode != 0:
            raise AgentExecError(f"agent exited with code {completed.returncode}")
        if not completed.stdout.strip():
            raise AgentExecError("agent produced empty output")
        return completed.stdout


class HttpAgentClient(AgentClient):
    """openai-compatible 檔位：stdlib urllib 直呼 /v1/chat/completions。

    api_key 由 env 名解析（config 永不放值）；缺 key 時不帶 Authorization
    （地端 ollama/vLLM 常無需驗證）。
    """

    def __init__(self, base_url: str, model: str, *, api_key_env: str | None = None,
                 timeout: int = 600, max_tokens: int = 8192) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key_env = api_key_env
        self._timeout = timeout
        self._max_tokens = max_tokens

    def run(self, prompt: str) -> str:
        import json as _json
        import urllib.error
        import urllib.request

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key_env:
            key = os.environ.get(self._api_key_env, "").strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=_json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = _json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AgentExecError(f"openai-compatible endpoint unreachable: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentExecError("openai-compatible response missing choices[0].message.content") from exc
        if not str(content).strip():
            raise AgentExecError("agent produced empty output")
        return str(content)


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
        if path.exists():
            try:
                cached = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                pass
            else:
                if cached:
                    return cached
        output = self._inner.run(prompt)
        self._write_text_atomically(path, output)
        return output

    def run(self, prompt: str) -> str:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return self.run_cached(prompt, prompt_hash)

    def clear_cache_key(self, cache_key: str) -> None:
        try:
            self.cache_path_for_key(cache_key).unlink()
        except FileNotFoundError:
            return
