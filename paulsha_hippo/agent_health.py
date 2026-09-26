"""External agent profile 的持久健康狀態與退避（issue #157）。

``ExternalAgentRouter`` 的 circuit breaker 只活在單一 process 的記憶體裡、
60 秒就關閉；dream timer 每一輪都重建 router。因此「每次都必敗」的 profile
（例如 CLI 呼叫格式與外部 CLI 版本不合、憑證或訂閱失效）在每一輪、每一個
新 session 都會先被試一次。本模組把這類**確定性**失敗寫進 memory root 下的
小型 JSON 狀態檔，並以指數退避讓 router 在退避期間直接略過該 profile（留下
帶原因的略過紀錄），``hippo doctor`` 也從同一個檔案顯示狀態。

判定原則：只有「啟動即失敗、stderr 開頭就是 CLI 參數解析或憑證錯誤」才算
確定性失敗。timeout、invalid_output、quota、一般 exit 1 等都可能是暫時性或
與內容有關，不進退避——錯誤地退避最後一個可用的 profile 會讓 session 被
park，代價比多試一次高得多。比對只看 stderr 開頭的一小段，因為部分 CLI
（如 codex）會把整段 prompt 回顯到 stderr，session 內容裡出現的錯誤字樣不得
觸發退避。

健康狀態是建議性的：讀檔失敗、檔案毀損都視為「沒有狀態」；寫檔失敗只放棄
這次更新。刪除狀態檔即可立即重置所有 profile。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .agent_profiles import AgentProfile, AgentRunResult, sanitize_stderr

_LOG = logging.getLogger("paulsha_hippo.agent_health")

HEALTH_SCHEMA_VERSION = 1
BACKOFF_BASE_SECONDS = 3600
BACKOFF_CAP_SECONDS = 86_400
# 參數解析／憑證錯誤在 CLI 啟動後幾秒內就會出現；跑了很久才出現相同字樣的
# 失敗不是呼叫契約問題。
DETERMINISTIC_FAILURE_MAX_ELAPSED_SECONDS = 30.0
# 連續幾次同類確定性失敗才進入退避。呼叫格式錯誤完全確定，一次即退避；
# 憑證錯誤可能是 token 更新的瞬間競態，連續兩個 session 都失敗才退避。
PERSISTENT_FAILURE_THRESHOLDS: Mapping[str, int] = {"invocation": 1, "credential": 2}
# 只比對 sanitized stderr 的開頭這一段。
_SIGNATURE_WINDOW = 160
_LAST_STDERR_LIMIT = 200
_REASON_STDERR_LIMIT = 120
# 可選的「<launcher>:」與「error:」前綴之後，緊接著的錯誤類型。
_PREFIX = r"^(?:[\w.-]+:\s*)?(?:error:\s*)?"
_INVOCATION_RE = re.compile(
    _PREFIX
    + r"(?:invalid command format"
    r"|unknown (?:argument|option|flag|subcommand)"
    r"|unrecognized (?:arguments?|options?)"
    r"|unexpected argument)",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    _PREFIX
    # 可選的 HTTP 狀態碼，例如 ``Error: 401 Unauthorized``。
    + r"(?:\d{3}\s+)?"
    + r"(?:no authentication information"
    r"|not (?:logged|signed) in"
    r"|login required"
    r"|unauthori[sz]ed"
    r"|unauthenticated"
    r"|authentication (?:failed|required)"
    r"|invalid api key"
    r"|env file not readable"
    r"|[\w]*api[_ ]key\b[\w ]*? not set)",
    re.IGNORECASE,
)
# 這兩類是 router 自己的判斷（沒有執行 CLI），不提供任何健康訊號。
_NO_SIGNAL_CATEGORIES = frozenset({"budget", "ineligible"})


def profile_health_path(memory_root: str | os.PathLike[str]) -> Path:
    return Path(memory_root) / "runtime" / "agents" / "profile-health.json"


def persistent_failure_kind(result: AgentRunResult) -> str | None:
    """回傳確定性失敗類型（``invocation``／``credential``），其餘回 None。"""
    if result.failure_category is None or result.failure_category in _NO_SIGNAL_CATEGORIES:
        return None
    if result.exit_code is None or result.exit_code == 0:
        return None
    if float(result.elapsed_seconds) > DETERMINISTIC_FAILURE_MAX_ELAPSED_SECONDS:
        return None
    head = sanitize_stderr(result.stderr)[:_SIGNATURE_WINDOW]
    if _INVOCATION_RE.search(head):
        return "invocation"
    if _CREDENTIAL_RE.search(head):
        return "credential"
    return None


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProfileHealthStore:
    """``ExternalAgentRouter`` 的 ``ProfileHealth`` 實作（JSON 檔、原子寫入）。

    ``read_only=True``（dry-run）時只讀不寫。每次呼叫都重新讀檔，讓同時跑的
    doctor／atomize 看到一致的狀態；檔案很小，成本可忽略。
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] | None = None,
        read_only: bool = False,
    ) -> None:
        self._path = Path(path)
        self._clock = clock or time.time
        self._read_only = read_only

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("schema_version") != HEALTH_SCHEMA_VERSION:
            return {}
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in profiles.items()
            if isinstance(value, dict)
        }

    def _save(self, profiles: Mapping[str, Mapping[str, Any]]) -> None:
        if self._read_only:
            return
        payload = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "profiles": {key: dict(profiles[key]) for key in sorted(profiles)},
        }
        tmp = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            _LOG.warning("agent health: cannot persist %s: %s", self._path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass

    def entries(self) -> dict[str, dict[str, Any]]:
        return self._load()

    @staticmethod
    def _applies(entry: Mapping[str, Any], profile: AgentProfile) -> bool:
        # command 變了（例如 operator 修好 argv）→ 舊狀態不再套用。
        return entry.get("command_fingerprint") == profile.command_fingerprint()

    def blocked_reason(self, profile: AgentProfile) -> str | None:
        entry = self._load().get(profile.id)
        if not entry or not self._applies(entry, profile):
            return None
        blocked_until = entry.get("blocked_until")
        if not isinstance(blocked_until, (int, float)) or self._clock() >= blocked_until:
            return None
        kind = str(entry.get("kind", "unknown"))
        failures = int(entry.get("consecutive_failures", 0) or 0)
        detail = str(entry.get("last_stderr", ""))[:_REASON_STDERR_LIMIT]
        reason = f"backoff {kind} until {_utc(float(blocked_until))} ({failures} consecutive)"
        return f"{reason}: {detail}" if detail else reason

    def record_outcome(self, profile: AgentProfile, result: AgentRunResult) -> None:
        if self._read_only or result.failure_category in _NO_SIGNAL_CATEGORIES:
            return
        profiles = self._load()
        kind = persistent_failure_kind(result)
        if kind is None:
            # 成功，或是非確定性失敗：代表確定性狀況沒有重現，清掉狀態。
            if profile.id in profiles:
                del profiles[profile.id]
                self._save(profiles)
            return
        now = float(self._clock())
        previous = profiles.get(profile.id)
        if (
            previous
            and previous.get("kind") == kind
            and self._applies(previous, profile)
        ):
            failures = int(previous.get("consecutive_failures", 0) or 0) + 1
            first_failed_at = str(previous.get("first_failed_at") or _utc(now))
        else:
            failures = 1
            first_failed_at = _utc(now)
        threshold = int(PERSISTENT_FAILURE_THRESHOLDS.get(kind, 1))
        blocked_until: float | None = None
        if failures >= threshold:
            exponent = min(failures - threshold, 16)
            blocked_until = now + min(BACKOFF_BASE_SECONDS * (2 ** exponent), BACKOFF_CAP_SECONDS)
        profiles[profile.id] = {
            "kind": kind,
            "consecutive_failures": failures,
            "threshold": threshold,
            "first_failed_at": first_failed_at,
            "last_failed_at": _utc(now),
            "blocked_until": blocked_until,
            "blocked_until_utc": _utc(blocked_until) if blocked_until is not None else None,
            "command_fingerprint": profile.command_fingerprint(),
            "profile_revision": profile.revision,
            "last_failure_category": result.failure_category,
            "last_exit_code": result.exit_code,
            "last_stderr": sanitize_stderr(result.stderr)[:_LAST_STDERR_LIMIT],
        }
        self._save(profiles)

    def describe(self, profile: AgentProfile) -> str | None:
        """``hippo doctor`` 用的一行摘要；沒有狀態時回 None。"""
        entry = self._load().get(profile.id)
        if not entry:
            return None
        kind = str(entry.get("kind", "unknown"))
        failures = int(entry.get("consecutive_failures", 0) or 0)
        threshold = int(entry.get("threshold", 1) or 1)
        detail = str(entry.get("last_stderr", ""))[:_REASON_STDERR_LIMIT]
        blocked_until = entry.get("blocked_until")
        if not self._applies(entry, profile):
            state = f"stale({kind})：command 已變更，不再套用"
        elif isinstance(blocked_until, (int, float)) and self._clock() < blocked_until:
            state = f"backoff({kind}) until {_utc(float(blocked_until))} failures={failures}"
        elif isinstance(blocked_until, (int, float)):
            state = f"probe-pending({kind})：退避已到期，下一個 session 會再試一次 failures={failures}"
        else:
            state = f"degraded({kind}) failures={failures}/{threshold}"
        suffix = f' last="{detail}"' if detail else ""
        return f"health={state}{suffix}"


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DETERMINISTIC_FAILURE_MAX_ELAPSED_SECONDS",
    "HEALTH_SCHEMA_VERSION",
    "PERSISTENT_FAILURE_THRESHOLDS",
    "ProfileHealthStore",
    "persistent_failure_kind",
    "profile_health_path",
]
