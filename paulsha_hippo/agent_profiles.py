"""宣告式 external headless agent profiles 與 bounded fallback router。

Hippo 只負責把一個 frozen prompt 交給外部 CLI；OAuth、API key、endpoint
與登入生命週期永遠留在 CLI/launcher 邊界外。這個模組故意不提供 HTTP、TCP
或 credential-env 解析 API。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROUTER_CONTRACT_VERSION = "1"
RESPONSE_SCHEMA_VERSION = "1"
MIN_PROVIDER_CONTEXT = 32_768
FIXED_TIMEOUT_SECONDS = 300
FIXED_MAX_OUTPUT_TOKENS = 2_048
FIXED_MAX_ATTEMPTS = 6
FIXED_MAX_AGENT_CALLS = 6

_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_ALLOWED_PLACEHOLDERS = {"MODEL", "EFFORT"}
_FORBIDDEN_TOKENS = {
    "--yolo",
    "--autopilot",
    "--dangerously-skip-permissions",
    "--no-sandbox",
}
_SHELL_TOKENS = {"bash", "sh", "zsh", "fish", "cmd", "powershell"}
_SHELL_META = set(";&|<>$`\\")
_CREDENTIAL_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|oauth|credential|private[_-]?key)",
    re.IGNORECASE,
)


class ProfileConfigError(ValueError):
    """Profile 設定不安全或不完整。"""


class AgentRunError(RuntimeError):
    """Router 不能完成一次 bounded external-agent attempt。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "process",
        profile_id: str | None = None,
        exit_code: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.profile_id = profile_id
        self.exit_code = exit_code
        self.stderr = stderr


def sanitize_stderr(value: object, *, limit: int = 500) -> str:
    """只留下 bounded、可持久化的 stderr evidence。"""
    text = str(value or "")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    text = re.sub(r"(?i)(bearer\s+|api[_-]?key\s*[=:]\s*|token\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return " ".join(text.split())[:limit]


def _tuple_strings(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ProfileConfigError(f"{field_name} must be a list of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProfileConfigError(f"{field_name} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if not result:
        raise ProfileConfigError(f"{field_name} must not be empty")
    return result


@dataclass(frozen=True)
class AgentProfile:
    id: str
    tier: int
    priority: int
    traits: tuple[str, ...]
    task_classes: tuple[str, ...]
    model: str
    effort: str
    supported_efforts: tuple[str, ...]
    argv: tuple[str, ...]
    timeout: int = FIXED_TIMEOUT_SECONDS
    provider_context: int = MIN_PROVIDER_CONTEXT
    revision: str = "1"
    native_fallback_disabled: bool = True
    zero_tool: bool = True
    no_mcp: bool = True
    no_custom_instructions: bool = True
    no_user_interaction: bool = True
    no_remote: bool = True
    fallback_on: tuple[str, ...] = (
        "ineligible",
        "auth",
        "rate_limit",
        "capacity",
        "timeout",
        "transport",
        "process",
        "empty_output",
        "invalid_output",
    )
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AgentProfile":
        forbidden_fields = {
            str(key) for key in raw
            if _CREDENTIAL_NAME_RE.search(str(key))
            or str(key).lower() in {"base_url", "provider_url", "oauth_state", "secret_path"}
        }
        if forbidden_fields:
            raise ProfileConfigError(
                "profile contains prohibited credential/provider field(s): "
                + ", ".join(sorted(forbidden_fields))
            )
        required = {"id", "tier", "priority", "traits", "task_classes", "model", "effort", "supported_efforts", "argv"}
        missing = sorted(required - set(raw))
        if missing:
            raise ProfileConfigError(f"profile missing fields: {', '.join(missing)}")
        profile_id = raw["id"]
        if not isinstance(profile_id, str) or not profile_id or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", profile_id):
            raise ProfileConfigError("profile.id must be a lowercase identifier")
        try:
            tier = int(raw["tier"])
            priority = int(raw["priority"])
        except (TypeError, ValueError) as exc:
            raise ProfileConfigError(f"profile {profile_id}: tier/priority must be integers") from exc
        if tier not in {1, 2, 3} or priority < 0:
            raise ProfileConfigError(f"profile {profile_id}: invalid tier/priority")
        model = raw["model"]
        effort = raw["effort"]
        if not isinstance(model, str) or not model.strip() or not isinstance(effort, str) or not effort.strip():
            raise ProfileConfigError(f"profile {profile_id}: model/effort must be non-empty strings")
        supported = _tuple_strings(raw["supported_efforts"], f"profile {profile_id}.supported_efforts")
        if effort not in supported:
            raise ProfileConfigError(f"profile {profile_id}: effort is not supported")
        argv = _validate_argv(_tuple_strings(raw["argv"], f"profile {profile_id}.argv"), profile_id)
        timeout = int(raw.get("timeout", FIXED_TIMEOUT_SECONDS))
        provider_context = int(raw.get("provider_context", MIN_PROVIDER_CONTEXT))
        if timeout != FIXED_TIMEOUT_SECONDS:
            raise ProfileConfigError(f"profile {profile_id}: timeout is fixed at {FIXED_TIMEOUT_SECONDS}")
        if provider_context < MIN_PROVIDER_CONTEXT:
            raise ProfileConfigError(f"profile {profile_id}: provider_context must be at least {MIN_PROVIDER_CONTEXT}")
        bool_fields = (
            "native_fallback_disabled", "zero_tool", "no_mcp", "no_custom_instructions",
            "no_user_interaction", "no_remote",
        )
        for field_name in bool_fields:
            if raw.get(field_name, True) is not True:
                raise ProfileConfigError(f"profile {profile_id}: {field_name} must be true")
        return cls(
            id=profile_id,
            tier=tier,
            priority=priority,
            traits=_tuple_strings(raw["traits"], f"profile {profile_id}.traits"),
            task_classes=_tuple_strings(raw["task_classes"], f"profile {profile_id}.task_classes"),
            model=model.strip(),
            effort=effort.strip(),
            supported_efforts=supported,
            argv=argv,
            timeout=timeout,
            provider_context=provider_context,
            revision=str(raw.get("revision", "1")),
            fallback_on=_tuple_strings(raw.get("fallback_on", cls.fallback_on), f"profile {profile_id}.fallback_on"),
            # Unknown values are intentionally not retained: profile objects are
            # later serialized into provenance/cache identity and must not become
            # an accidental secret/config sink.
            metadata={},
        )

    def render_argv(self) -> tuple[str, ...]:
        rendered: list[str] = []
        for token in self.argv:
            if token == "{MODEL}":
                rendered.append(self.model)
            elif token == "{EFFORT}":
                rendered.append(self.effort)
            else:
                rendered.append(token)
        return tuple(rendered)

    def command_fingerprint(self) -> str:
        return fingerprint_argv(self.render_argv())

    def eligible(self, *, task_class: str = "atomization", path: str | None = None) -> tuple[bool, str]:
        if task_class not in self.task_classes:
            return False, "task_class"
        if self.provider_context < MIN_PROVIDER_CONTEXT:
            return False, "context_budget"
        if not self.native_fallback_disabled:
            return False, "native_fallback"
        if not all((self.zero_tool, self.no_mcp, self.no_custom_instructions, self.no_user_interaction, self.no_remote)):
            return False, "unsafe_restriction"
        executable = self.render_argv()[0]
        if path is not None:
            executable = str(Path(path) / executable)
        if Path(executable).is_absolute():
            if not Path(executable).is_file() or not os.access(executable, os.X_OK):
                return False, "executable"
        elif shutil.which(executable) is None:
            return False, "executable"
        return True, "eligible"


def _validate_argv(argv: tuple[str, ...], profile_id: str) -> tuple[str, ...]:
    if not argv:
        raise ProfileConfigError(f"profile {profile_id}.argv must not be empty")
    for index, token in enumerate(argv):
        if not token or any(char in token for char in _SHELL_META):
            raise ProfileConfigError(f"profile {profile_id}.argv[{index}] contains shell syntax")
        if "{PROMPT}" in token or "PROMPT" in token:
            raise ProfileConfigError(f"profile {profile_id}: prompt must be supplied through stdin")
        placeholders = _PLACEHOLDER_RE.findall(token)
        if placeholders and (token not in {"{MODEL}", "{EFFORT}"} or any(name not in _ALLOWED_PLACEHOLDERS for name in placeholders)):
            raise ProfileConfigError(f"profile {profile_id}: only complete-token MODEL/EFFORT placeholders are allowed")
        token_name = Path(token).name.lower()
        flag_name = token.lower().split("=", 1)[0]
        if token_name in _SHELL_TOKENS or flag_name in _FORBIDDEN_TOKENS:
            raise ProfileConfigError(f"profile {profile_id}: forbidden executable/flag {token}")
    if len(argv) >= 2 and argv[0].split("/")[-1] in _SHELL_TOKENS and argv[1] in {"-c", "-lc"}:
        raise ProfileConfigError(f"profile {profile_id}: shell wrapper is forbidden")
    if any(token in _FORBIDDEN_TOKENS for token in argv):
        raise ProfileConfigError(f"profile {profile_id}: permission bypass is forbidden")
    return argv


def default_profiles() -> tuple[AgentProfile, ...]:
    rows = (
        ("claude", 1, 10, ("judge", "reasoner"), ("atomization", "title", "skillopt"), "claude-sonnet", "high", ("medium", "high", "xhigh"), ("claude", "--model", "{MODEL}", "--effort", "{EFFORT}", "--print")),
        ("codex", 1, 20, ("judge", "reasoner"), ("atomization", "title", "skillopt"), "gpt-5", "high", ("medium", "high", "xhigh"), ("codex", "exec", "--model", "{MODEL}", "--reasoning-effort", "{EFFORT}", "--sandbox", "read-only", "--skip-git-repo-check", "-")),
        ("agy", 2, 10, ("fast", "responsive"), ("atomization", "title", "skillopt"), "default", "medium", ("low", "medium", "high"), ("agy", "--model", "{MODEL}", "--effort", "{EFFORT}", "--headless", "--stdin")),
        ("cg", 2, 20, ("heavy-implementation", "fast"), ("atomization", "title", "skillopt"), "default", "high", ("medium", "high", "xhigh"), ("cg", "--model", "{MODEL}", "--effort", "{EFFORT}", "--headless", "--stdin")),
        ("co-gem", 3, 10, ("low-cost", "fallback"), ("atomization", "title", "skillopt"), "local", "low", ("low", "medium"), ("co-gem", "--model", "{MODEL}", "--effort", "{EFFORT}", "--headless", "--stdin")),
        ("claude-gem", 3, 20, ("low-cost", "fallback"), ("atomization", "title", "skillopt"), "local", "low", ("low", "medium"), ("claude-gem", "--model", "{MODEL}", "--effort", "{EFFORT}", "--headless", "--stdin")),
    )
    return tuple(AgentProfile(
        id=profile_id, tier=tier, priority=priority, traits=traits, task_classes=tasks,
        model=model, effort=effort, supported_efforts=efforts, argv=argv,
    ) for profile_id, tier, priority, traits, tasks, model, effort, efforts, argv in rows)


def profiles_from_config(value: object) -> tuple[AgentProfile, ...]:
    if value is None:
        return default_profiles()
    if isinstance(value, Mapping):
        values = []
        for profile_id, raw in value.items():
            if not isinstance(raw, Mapping):
                raise ProfileConfigError(f"profile {profile_id} must be a mapping")
            values.append({"id": profile_id, **raw})
    elif isinstance(value, Sequence) and not isinstance(value, str):
        values = list(value)
    else:
        raise ProfileConfigError("external_agents.profiles must be a list or mapping")
    profiles = tuple(AgentProfile.from_mapping(raw) for raw in values if isinstance(raw, Mapping))
    if len(profiles) != len(values):
        raise ProfileConfigError("external_agents.profiles contains a non-mapping")
    validate_profiles(profiles)
    return profiles


def validate_profiles(profiles: Sequence[AgentProfile]) -> None:
    ids = [profile.id for profile in profiles]
    if len(set(ids)) != len(ids):
        raise ProfileConfigError("profile IDs must be unique")
    ordered = sorted(profiles, key=lambda profile: (profile.tier, profile.priority, profile.id))
    if [profile.id for profile in ordered] != ids and len(profiles) > 1:
        # Configuration order is not routing authority; accepting it is fine, but
        # duplicate tier/priority would make behavior ambiguous.
        pass
    if any(profile.priority == other.priority and profile.tier == other.tier and profile.id != other.id for profile in profiles for other in profiles):
        raise ProfileConfigError("same-tier priorities must be unique")
    for profile in profiles:
        _validate_argv(profile.argv, profile.id)


def fingerprint_argv(argv: Sequence[str]) -> str:
    """Hash command identity without persisting a personal absolute path."""
    normalized = []
    for token in argv:
        value = str(token)
        if os.path.isabs(value):
            value = Path(value).name
        normalized.append(value)
    return hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode("utf-8")).hexdigest()


def cache_identity(
    *,
    operation: str,
    profile: AgentProfile,
    config_hash: str,
    skill_hash: str,
    prompt_hash: str,
    response_schema: str = RESPONSE_SCHEMA_VERSION,
    fallback_reason: str | None = None,
) -> str:
    payload = {
        "operation": operation,
        "response_schema": response_schema,
        "router_contract": ROUTER_CONTRACT_VERSION,
        "profile_id": profile.id,
        "profile_revision": profile.revision,
        "tier": profile.tier,
        "model": profile.model,
        "effort": profile.effort,
        "command_fingerprint": profile.command_fingerprint(),
        "config_hash": config_hash,
        "skill_hash": skill_hash,
        "prompt_hash": prompt_hash,
        "fallback_reason": fallback_reason,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentRunResult:
    profile_id: str
    profile_revision: str
    tier: int
    attempt_index: int
    requested_model: str
    requested_effort: str
    observed_model: str | None
    model_verification: str
    command_fingerprint: str
    elapsed_seconds: float
    failure_category: str | None = None
    stderr: str = ""
    exit_code: int | None = None
    fallback_reason: str | None = None


def child_environment(extra: Mapping[str, str] | None = None, *, path: str | None = None) -> dict[str, str]:
    """固定最小 non-secret env；不繼承 parent os.environ。"""
    result = {
        "PATH": path or os.environ.get("PATH", os.defpath),
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HIPPO_SELF_SESSION": "1",
    }
    for key, value in (extra or {}).items():
        key = str(key)
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "HIPPO_SELF_SESSION", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "TERM"}
        # The output-limit variable contains the word TOKEN but is a fixed,
        # non-secret runtime budget.  Check the explicit allowlist first so the
        # generic credential-name guard cannot reject it accidentally.
        if key not in allowed:
            raise ProfileConfigError(f"environment variable is not in the minimal allowlist: {key}")
        if _CREDENTIAL_NAME_RE.search(key) and key not in {"CLAUDE_CODE_MAX_OUTPUT_TOKENS"}:
            raise ProfileConfigError(f"credential environment passthrough is forbidden: {key}")
        result[key] = str(value)
    return result


class ExternalAgentRouter:
    """Deterministic tier/priority fallback with bounded attempts and circuit state."""

    def __init__(
        self,
        profiles: Sequence[AgentProfile],
        *,
        task_class: str = "atomization",
        deadline_seconds: int = FIXED_TIMEOUT_SECONDS,
        max_attempts: int = FIXED_MAX_ATTEMPTS,
        max_agent_calls: int = FIXED_MAX_AGENT_CALLS,
        executor: Callable[[AgentProfile, str, int], tuple[str, str, int | None]] | None = None,
    ) -> None:
        validate_profiles(profiles)
        self.profiles = tuple(sorted(profiles, key=lambda profile: (profile.tier, profile.priority, profile.id)))
        self.task_class = task_class
        self.deadline_seconds = int(deadline_seconds)
        self.max_attempts = int(max_attempts)
        self.max_agent_calls = int(max_agent_calls)
        if self.deadline_seconds <= 0 or self.max_attempts <= 0 or self.max_agent_calls <= 0:
            raise ProfileConfigError("router budgets must be positive")
        self._circuit_open_until: dict[str, float] = {}
        self._failures: list[AgentRunResult] = []
        self._last_error: AgentRunError | None = None
        self.last_result: AgentRunResult | None = None
        self.attempts: tuple[AgentRunResult, ...] = ()
        self._executor = executor

    def cache_namespace(self) -> str:
        """Return a profile-set identity for cache separation.

        A prompt cache must not be replayed after a profile/model/effort/argv
        change.  The namespace contains only declarative non-secret contract
        fields; it never includes an executable path or prompt text.
        """
        payload = {
            "router_contract": ROUTER_CONTRACT_VERSION,
            "task_class": self.task_class,
            "deadline_seconds": self.deadline_seconds,
            "max_attempts": self.max_attempts,
            "max_agent_calls": self.max_agent_calls,
            "profiles": [
                {
                    "id": profile.id,
                    "revision": profile.revision,
                    "tier": profile.tier,
                    "priority": profile.priority,
                    "model": profile.model,
                    "effort": profile.effort,
                    "command_fingerprint": profile.command_fingerprint(),
                }
                for profile in self.profiles
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _run_one(self, profile: AgentProfile, prompt: str, attempt_index: int) -> tuple[str, str, int | None]:
        if self._executor is not None:
            return self._executor(profile, prompt, attempt_index)
        from .atomizer.agent_exec import AgentExecClient

        client = AgentExecClient(list(profile.render_argv()), timeout=profile.timeout, profile=profile)
        return client.run_with_evidence(prompt)

    def run(self, prompt: str) -> str:
        self.last_result = None
        started = time.monotonic()
        attempts: list[AgentRunResult] = []
        calls = 0
        for profile in self.profiles:
            if len(attempts) >= self.max_attempts or calls >= self.max_agent_calls:
                break
            if time.monotonic() - started >= self.deadline_seconds:
                break
            open_until = self._circuit_open_until.get(profile.id, 0.0)
            if open_until > time.monotonic():
                continue
            eligible, reason = profile.eligible(task_class=self.task_class)
            if not eligible:
                attempts.append(AgentRunResult(
                    profile.id, profile.revision, profile.tier, len(attempts) + 1,
                    profile.model, profile.effort, None, "unavailable", profile.command_fingerprint(),
                    0.0, "ineligible", reason, None, None,
                ))
                continue
            index = len(attempts) + 1
            calls += 1
            attempt_started = time.monotonic()
            try:
                raw, stderr, exit_code = self._run_one(profile, prompt, index)
                elapsed = time.monotonic() - attempt_started
                if not str(raw).strip():
                    raise AgentRunError("external agent produced empty output", category="empty_output", profile_id=profile.id, stderr=stderr)
                result = AgentRunResult(
                    profile.id, profile.revision, profile.tier, index, profile.model, profile.effort,
                    None, "unverified", profile.command_fingerprint(), elapsed,
                    None, sanitize_stderr(stderr), exit_code,
                    None if not attempts else "degraded-success",
                )
                self.last_result = result
                attempts.append(result)
                self.attempts = tuple(attempts)
                return str(raw)
            except Exception as exc:
                if not isinstance(exc, AgentRunError):
                    # Avoid a module-level import cycle: agent_exec imports the
                    # profile contract, while the router lazily invokes it.
                    category = str(getattr(exc, "category", "process"))
                    exc = AgentRunError(
                        str(exc),
                        category=category,
                        profile_id=profile.id,
                        exit_code=getattr(exc, "exit_code", None),
                        stderr=getattr(exc, "stderr", ""),
                    )
                category = exc.category if exc.category in profile.fallback_on else "policy"
                self._last_error = exc
                result = AgentRunResult(
                    profile.id, profile.revision, profile.tier, index, profile.model, profile.effort,
                    None, "unavailable", profile.command_fingerprint(), time.monotonic() - attempt_started,
                    category, sanitize_stderr(exc.stderr), exc.exit_code,
                )
                attempts.append(result)
                self._circuit_open_until[profile.id] = time.monotonic() + 60.0
                if category not in profile.fallback_on:
                    break
        self.attempts = tuple(attempts)
        if not attempts and self._last_error is not None:
            raise AgentRunError(
                str(self._last_error),
                category=self._last_error.category,
                profile_id=self._last_error.profile_id,
                exit_code=self._last_error.exit_code,
                stderr=self._last_error.stderr,
            )
        last = attempts[-1] if attempts else None
        category = last.failure_category if last else "ineligible"
        detail = "external agent fallback exhausted"
        if last is not None:
            detail += f" ({category or 'process'}"
            if last.exit_code is not None:
                detail += f", exit {last.exit_code}"
            detail += ")"
            evidence: list[str] = []
            for attempt in attempts:
                parts = [attempt.profile_id]
                if attempt.exit_code is not None:
                    parts.append(f"exit {attempt.exit_code}")
                if attempt.stderr:
                    parts.append(attempt.stderr)
                if len(parts) > 1:
                    evidence.append(" ".join(parts))
            if evidence:
                detail += ": " + " | ".join(evidence)[:500]
        raise AgentRunError(
            detail,
            category=category or "process",
            profile_id=last.profile_id if last else None,
            exit_code=last.exit_code if last else None,
            stderr=last.stderr if last else "",
        )


__all__ = [
    "AgentProfile", "AgentRunError", "AgentRunResult", "ExternalAgentRouter",
    "ProfileConfigError", "ROUTER_CONTRACT_VERSION", "RESPONSE_SCHEMA_VERSION",
    "cache_identity", "child_environment", "default_profiles", "fingerprint_argv",
    "profiles_from_config", "sanitize_stderr", "validate_profiles",
]
