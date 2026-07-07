"""Per-session <=20-char zh-TW title generation via local gemma4, with offline fallback.

The title is generated at import time and stored as the session's ``assistant_summary``
(rendered under ## Summary) plus a ``title_source`` marker (gemma4 | fallback). Title
generation never blocks or fails the import: if the LLM backend is unavailable the title
falls back to the first user prompt truncated, marked ``fallback`` for later regeneration.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from paulsha_hippo.atomizer import config as atomizer_config

_MAX = 20
_PROMPT = (
    "請用繁體中文為以下工作 session 下一個標題，最多 20 個字、單行、不要標點或引號：\n\n"
    "使用者需求：{prompt}\n\n助理結論：{summary}\n\n標題："
)
_ATOM_PROMPT = (
    "請用繁體中文為以下筆記內容下一個精簡標題，最多 20 個字、單行、不要標點或引號：\n\n"
    "{body}\n\n標題："
)


def _truncate(text: str, limit: int = _MAX) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _backend_settings() -> tuple[tuple[str, ...], str]:
    return atomizer_config.resolve_agent_exec_settings()


def _gemma4_reachable(timeout: float = 1.0) -> bool:
    """Fast TCP pre-check on the gemma4 upstream so an unreachable backend fails over
    to the fallback title instantly instead of blocking on a long subprocess timeout.

    Targets the same upstream as scripts/claude-gemma4-proxy (the real backend behind
    the local proxy), not the proxy port — the wrapper starts the proxy on demand, so
    only the upstream reliably reflects whether a title can actually be generated.
    """
    _, upstream = _backend_settings()
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        # Malformed upstream URL → treat as unreachable so we fall back, instead of
        # accidentally probing localhost and then blocking on the subprocess timeout.
        return False
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _default_runner(text: str, command: tuple[str, ...], timeout: int) -> str:
    _, upstream = _backend_settings()
    if not _gemma4_reachable():
        raise RuntimeError("gemma4 backend not reachable")
    proc = subprocess.run(
        list(command),
        input=text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            **os.environ,
            **atomizer_config.build_agent_exec_env(upstream_url=upstream),
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gemma4 exit {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout


def generate_title(
    session: dict[str, Any],
    *,
    command: tuple[str, ...] | None = None,
    timeout: int = 60,
    runner: Callable[[str, tuple[str, ...], int], str] | None = None,
) -> tuple[str, str]:
    """Return (title, source). source is 'gemma4' on success, 'fallback' otherwise."""
    prompts = session.get("user_prompts") or []
    first_prompt = prompts[0] if prompts else ""
    summary = session.get("assistant_summary") or ""
    if not first_prompt.strip() and not summary.strip():
        # Nothing to title — don't feed the LLM an empty prompt; it answers with a
        # complaint that would get stored as a junk title. Use a neutral marker.
        return "(無內容)", "fallback"
    if command is None:
        command, _ = _backend_settings()
    runner = runner or _default_runner
    text = _PROMPT.format(prompt=first_prompt[:500], summary=summary[:500])
    try:
        title = _truncate(runner(text, command, timeout))
        if title:
            return title, "gemma4"
    except Exception:
        pass
    return _truncate(first_prompt) or _truncate(summary) or "(無內容)", "fallback"


def generate_atom_title(
    body: str,
    *,
    command: tuple[str, ...] | None = None,
    timeout: int = 60,
    runner: Callable[[str, tuple[str, ...], int], str] | None = None,
) -> tuple[str | None, str]:
    """Distill a <=20-char zh-TW title from a note body via gemma4.

    Returns (title, source). On a reachable backend that yields a non-empty title,
    source is 'gemma4'. When the backend is offline or yields nothing usable,
    returns (None, 'offline') so callers can skip rather than stamp a junk title —
    a one-shot retitle migration must not invent titles when the LLM is down (#151).
    """
    if not body.strip():
        return None, "offline"
    if command is None:
        command, _ = _backend_settings()
    runner = runner or _default_runner
    text = _ATOM_PROMPT.format(body=body[:1000])
    try:
        title = _truncate(runner(text, command, timeout))
    except Exception:
        return None, "offline"
    if title:
        return title, "gemma4"
    return None, "offline"


def _cache_path(memory_root: str | Path, session_id: str) -> Path:
    safe = re.sub(r"[\\/]+", "__", (session_id or "_unknown"))
    return Path(memory_root) / "runtime" / "cache" / "title" / f"{safe}.json"


def apply(session: dict[str, Any], *, memory_root: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Generate (or reuse cached) title and set session['assistant_summary'] + 'title_source'."""
    cache = _cache_path(memory_root, session.get("session_id") or "")
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            session["assistant_summary"] = cached["title"]
            session["title_source"] = cached["source"]
            return session
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    title, source = generate_title(session, **kwargs)
    if source == "gemma4":
        # Only cache successful LLM titles. Fallback titles are deterministic and
        # left uncached so they regenerate (and upgrade) once gemma4 is reachable.
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f".{cache.name}.tmp")
        tmp.write_text(json.dumps({"title": title, "source": source}), encoding="utf-8")
        tmp.replace(cache)
    session["assistant_summary"] = title
    session["title_source"] = source
    return session
