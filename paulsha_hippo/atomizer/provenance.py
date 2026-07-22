"""可持久化的 external-agent distillation provenance。

所有欄位都以 hash/label 表示；這裡沒有 credential、provider URL 或原始 prompt
的持久化通道。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .. import __version__
from ..agent_profiles import AgentRunResult, fingerprint_argv, sanitize_stderr


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


def provenance_from_result(
    result: AgentRunResult | None,
    *,
    config_hash: str = "",
    skill_hash: str = "",
    hippo_version: str = __version__,
    build: str | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    if result is None:
        return {
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
            "build_commit": build or "unknown",
            "fallback_reason": fallback_reason,
        }
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
        "config_hash": config_hash,
        "skill_hash": skill_hash,
        "hippo_version": hippo_version,
        "build_commit": build or "unknown",
        "fallback_reason": fallback_reason or result.fallback_reason,
        "elapsed_seconds": round(float(result.elapsed_seconds), 6),
        "failure_category": result.failure_category,
        "stderr": sanitize_stderr(result.stderr),
        "exit_code": result.exit_code,
    }


def provenance_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """只保留 schema 欄位，避免外部 CLI 回傳任意 raw data 落入 note。"""
    allowed = {
        "profile_id", "profile_revision", "tier", "priority", "attempt_index", "requested_model",
        "requested_effort", "observed_model", "model_verification", "command_fingerprint",
        "config_hash", "skill_hash", "hippo_version", "build_commit", "fallback_reason",
        "elapsed_seconds", "failure_category", "stderr", "exit_code",
    }
    result = {key: value[key] for key in sorted(allowed) if key in value}
    if "stderr" in result:
        result["stderr"] = sanitize_stderr(result["stderr"])
    return result


__all__ = ["build_commit", "command_fingerprint", "provenance_from_result", "provenance_hash", "safe_provenance", "sha256_text"]
