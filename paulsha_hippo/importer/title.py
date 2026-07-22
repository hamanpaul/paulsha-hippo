"""Per-capture title generation through the shared external-agent contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from paulsha_hippo import paths
from paulsha_hippo.agent_profiles import (
    AgentProfile,
    ExternalAgentRouter,
    ProfileConfigError,
    fingerprint_argv,
)
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


def _custom_profile(command: tuple[str, ...]) -> AgentProfile:
    """Build the explicit test/operator profile without touching runtime config."""
    return AgentProfile.from_mapping(
        {
            "id": "title-custom",
            "tier": 3,
            "priority": 0,
            "traits": ["custom-title"],
            "task_classes": ["title"],
            "model": "custom",
            "effort": "medium",
            "supported_efforts": ["medium"],
            "argv": list(command),
        }
    )


def _configured_router(config: atomizer_config.AtomizerConfig, *, timeout: int) -> ExternalAgentRouter:
    deadline = config.router_deadline_seconds
    if timeout > 0:
        deadline = min(int(timeout), deadline)
    return ExternalAgentRouter(
        config.external_profiles,
        task_class="title",
        deadline_seconds=deadline,
        max_attempts=config.router_max_attempts,
        max_agent_calls=config.router_max_agent_calls,
    )


def _default_runner(text: str, command: tuple[str, ...] | None, timeout: int) -> str:
    """Run title work via the same bounded tier router as atomization.

    A supplied command is an explicit test/operator CLI override.  The normal
    path uses configured profiles so title generation cannot bypass fallback,
    stdin-only prompts, or the minimal child environment.
    """
    if command is None:
        # The importer runtime has one authority: the managed canonical config.
        # In particular, do not recover from a broken policy/config by silently
        # reconstructing the legacy/default profile set.
        config, _ = atomizer_config.load_config()
        return _configured_router(config, timeout=timeout).run(text)
    custom_profile = _custom_profile(tuple(command))
    return ExternalAgentRouter(
        (custom_profile,),
        task_class="title",
        deadline_seconds=min(max(int(timeout), 1), 300),
        max_attempts=1,
        max_agent_calls=1,
    ).run(text)


def generate_title(
    session: dict[str, Any],
    *,
    command: tuple[str, ...] | None = None,
    timeout: int = 60,
    runner: Callable[[str, tuple[str, ...], int], str] | None = None,
) -> tuple[str, str]:
    """Return ``(title, source)``; successful external CLI output is auditable."""
    prompts = session.get("user_prompts") or []
    first_prompt = prompts[0] if prompts else ""
    summary = session.get("assistant_summary") or ""
    if not first_prompt.strip() and not summary.strip():
        return "(無內容)", "fallback"
    text = _PROMPT.format(prompt=first_prompt[:500], summary=summary[:500])
    try:
        if runner is None:
            title = _truncate(_default_runner(text, command, timeout))
        else:
            effective_command = tuple(command or ())
            title = _truncate(runner(text, effective_command, timeout))
        if title:
            return title, "external-agent"
    except (atomizer_config.AtomizerConfigError, ProfileConfigError):
        raise
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
    """Generate an atom title through the shared external CLI router."""
    if not body.strip():
        return None, "offline"
    text = _ATOM_PROMPT.format(body=body[:1000])
    try:
        if runner is None:
            title = _truncate(_default_runner(text, command, timeout))
        else:
            effective_command = tuple(command or ())
            title = _truncate(runner(text, effective_command, timeout))
    except (atomizer_config.AtomizerConfigError, ProfileConfigError):
        raise
    except Exception:
        return None, "offline"
    return (title, "external-agent") if title else (None, "offline")


def _title_input_hash(session: dict[str, Any]) -> str:
    messages = session.get("assistant_messages")
    if not isinstance(messages, list):
        messages = [session.get("assistant_summary") or ""]
    payload = {
        "user_prompts": list(session.get("user_prompts") or []),
        "assistant_messages": list(messages),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path(memory_root: str | Path, session_id: str, input_hash: str, cache_identity: str) -> Path:
    safe = re.sub(r"[\\/]+", "__", (session_id or "_unknown"))
    return (
        Path(memory_root)
        / "runtime"
        / "cache"
        / "title"
        / f"{safe}--{input_hash}--{cache_identity[:16]}.json"
    )


def _cache_context(command: tuple[str, ...] | None) -> tuple[str, str, str]:
    """Return config, skill/prompt, and router identities for title cache keys."""
    skill_hash = hashlib.sha256(
        (_PROMPT + "\n" + _ATOM_PROMPT).encode("utf-8")
    ).hexdigest()
    canonical_path = paths.atomizer_config_path()
    if not canonical_path.is_file():
        if command is None:
            # Tests/operator-injected runners may exercise cache behavior before
            # `hippo init`; this identity is deliberately not a profile fallback.
            return "canonical-unavailable", skill_hash, "canonical-router-unavailable"
        router = ExternalAgentRouter(
            (_custom_profile(tuple(command)),),
            task_class="title",
            max_attempts=1,
            max_agent_calls=1,
        )
        return "canonical-unavailable", skill_hash, router.cache_namespace()

    config, config_hash = atomizer_config.load_config()
    if command is None or any(
        tuple(command) == profile.render_argv() for profile in config.external_profiles
    ):
        router = _configured_router(config, timeout=config.router_deadline_seconds)
    else:
        router = ExternalAgentRouter(
            (_custom_profile(tuple(command)),), task_class="title", max_attempts=1, max_agent_calls=1
        )
    return config_hash, skill_hash, router.cache_namespace()


def apply(session: dict[str, Any], *, memory_root: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Generate/reuse a title without mutating assistant semantic content."""
    input_hash = _title_input_hash(session)
    command_value = kwargs.get("command")
    command = tuple(command_value) if command_value is not None else None
    command_identity = fingerprint_argv(command) if command is not None else "canonical-router"
    config_hash, skill_hash, router_identity = _cache_context(command)
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "operation": "session-title",
                "response_schema": "1",
                "input_hash": input_hash,
                "command_fingerprint": command_identity,
                "router_namespace": router_identity,
                "config_hash": config_hash,
                "skill_hash": skill_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cache = _cache_path(memory_root, session.get("session_id") or "", input_hash, cache_key)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if (
                cached.get("input_hash") != input_hash
                or cached.get("command_fingerprint") != command_identity
                or cached.get("router_namespace") != router_identity
                or cached.get("config_hash") != config_hash
                or cached.get("skill_hash") != skill_hash
            ):
                raise KeyError("cache identity")
            session["session_title"] = cached["title"]
            session["title_source"] = cached["source"]
            return session
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    title, source = generate_title(session, **kwargs)
    if source == "external-agent":
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f".{cache.name}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "title": title,
                    "source": source,
                    "input_hash": input_hash,
                    "command_fingerprint": command_identity,
                    "router_namespace": router_identity,
                    "config_hash": config_hash,
                    "skill_hash": skill_hash,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(cache)
    session["session_title"] = title
    session["title_source"] = source
    return session
