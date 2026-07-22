"""可持久化的 external-agent distillation provenance。

所有欄位都以 hash/label 表示；這裡沒有 credential、provider URL 或原始 prompt
的持久化通道。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import __version__
from ..build_info import build_identity
from ..agent_profiles import (
    AgentRunResult,
    RESPONSE_SCHEMA_VERSION,
    fingerprint_argv,
    sanitize_stderr,
)

_MAX_PROVENANCE_ATTEMPTS = 6


def _attempt_payload(result: AgentRunResult) -> dict[str, Any]:
    """Project one bounded attempt without prompt/output/credential fields."""
    return {
        "profile_id": result.profile_id,
        "profile_revision": result.profile_revision,
        "tier": result.tier,
        "priority": result.priority,
        "attempt_index": result.attempt_index,
        "requested_model": result.requested_model,
        "requested_effort": result.requested_effort,
        "observed_model": result.observed_model,
        "model_verification": result.model_verification,
        "command_fingerprint": result.command_fingerprint,
        "response_schema": result.response_schema,
        "elapsed_seconds": round(float(result.elapsed_seconds), 6),
        "failure_category": result.failure_category,
        "fallback_reason": result.fallback_reason,
        "stderr": sanitize_stderr(result.stderr),
        "exit_code": result.exit_code,
    }


def _bounded_attempts(attempts: Sequence[AgentRunResult]) -> list[dict[str, Any]]:
    return [
        _attempt_payload(attempt)
        for attempt in tuple(attempts)[:_MAX_PROVENANCE_ATTEMPTS]
        if isinstance(attempt, AgentRunResult)
    ]


def sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def build_commit(*, repo_root: str | Path | None = None) -> str:
    """回報 build identity；git 不可用時明確回 unknown。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else "unknown"


def command_fingerprint(command: list[str] | tuple[str, ...]) -> str:
    return fingerprint_argv(command)


def _runtime_build_commit() -> str:
    """Resolve the same embedded/env build authority used by CLI and Dream."""
    try:
        value = str(build_identity().get("build_commit") or "").strip()
    except Exception:  # pragma: no cover - provenance must remain persistable
        return "unknown"
    return value or "unknown"


def provenance_from_result(
    result: AgentRunResult | None,
    *,
    config_hash: str = "",
    skill_hash: str = "",
    hippo_version: str = __version__,
    build: str | None = None,
    fallback_reason: str | None = None,
    attempts: Sequence[AgentRunResult] | None = None,
) -> dict[str, Any]:
    resolved_build = build if build is not None else _runtime_build_commit()
    if result is None:
        payload = {
            "profile_id": "unknown",
            "profile_revision": "unknown",
            "tier": None,
            "priority": None,
            "attempt_index": None,
            "requested_model": None,
            "requested_effort": None,
            "observed_model": None,
            "model_verification": "unavailable",
            "command_fingerprint": "unknown",
            "config_hash": config_hash,
            "skill_hash": skill_hash,
            "hippo_version": hippo_version,
            "build_commit": resolved_build or "unknown",
            "fallback_reason": fallback_reason,
            "response_schema": RESPONSE_SCHEMA_VERSION,
        }
        if attempts is not None:
            payload["attempts"] = _bounded_attempts(attempts)
        return payload
    payload = {
        "profile_id": result.profile_id,
        "profile_revision": result.profile_revision,
        "tier": result.tier,
        "priority": result.priority,
        "attempt_index": result.attempt_index,
        "requested_model": result.requested_model,
        "requested_effort": result.requested_effort,
        "observed_model": result.observed_model,
        "model_verification": result.model_verification,
        "command_fingerprint": result.command_fingerprint,
        "config_hash": config_hash,
        "skill_hash": skill_hash,
        "hippo_version": hippo_version,
        "build_commit": resolved_build or "unknown",
        "fallback_reason": fallback_reason or result.fallback_reason,
        "response_schema": result.response_schema,
        "elapsed_seconds": round(float(result.elapsed_seconds), 6),
        "failure_category": result.failure_category,
        "stderr": sanitize_stderr(result.stderr),
        "exit_code": result.exit_code,
    }
    if attempts is not None:
        payload["attempts"] = _bounded_attempts(attempts)
    return payload


def provenance_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """只保留 schema 欄位，避免外部 CLI 回傳任意 raw data 落入 note。"""
    allowed = {
        "profile_id", "profile_revision", "tier", "priority", "attempt_index", "requested_model",
        "requested_effort", "observed_model", "model_verification", "command_fingerprint",
        "config_hash", "skill_hash", "hippo_version", "build_commit", "fallback_reason",
        "response_schema", "elapsed_seconds", "failure_category", "stderr", "exit_code",
        "attempts",
    }
    result = {key: value[key] for key in sorted(allowed) if key in value}
    if "stderr" in result:
        result["stderr"] = sanitize_stderr(result["stderr"])
    if "attempts" in result:
        bounded: list[dict[str, Any]] = []
        raw_attempts = result["attempts"]
        if isinstance(raw_attempts, Sequence) and not isinstance(raw_attempts, (str, bytes)):
            attempt_allowed = {
                "profile_id", "profile_revision", "tier", "priority", "attempt_index",
                "requested_model", "requested_effort", "observed_model",
                "model_verification", "command_fingerprint", "response_schema",
                "elapsed_seconds", "failure_category", "fallback_reason", "stderr", "exit_code",
            }
            for attempt in list(raw_attempts)[:_MAX_PROVENANCE_ATTEMPTS]:
                if not isinstance(attempt, Mapping):
                    continue
                bounded_attempt = {
                    key: attempt[key]
                    for key in sorted(attempt_allowed)
                    if key in attempt
                }
                if "stderr" in bounded_attempt:
                    bounded_attempt["stderr"] = sanitize_stderr(bounded_attempt["stderr"])
                bounded.append(bounded_attempt)
        result["attempts"] = bounded
    return result


__all__ = ["build_commit", "command_fingerprint", "provenance_from_result", "provenance_hash", "safe_provenance", "sha256_text"]
