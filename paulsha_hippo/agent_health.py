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

狀態依 task class 分開（atomization、title、skillopt 各自記錄），dream、直呼
``hippo atomize``、title importer（hook）與 skillopt 的 router 共用同一個檔案，
寫入以檔案鎖序列化。健康狀態是建議性的：讀檔失敗、檔案毀損都視為「沒有
狀態」；寫檔或取鎖失敗只放棄這次更新。刪除狀態檔即可立即重置所有 profile。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
# 綁定模組載入時的 monotonic／sleep：router 測試會把 ``time.monotonic`` 換成
# 假時鐘，取鎖的期限不能跟著被凍結。
from time import monotonic as _monotonic, sleep as _sleep
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
# 寫入鎖的取得期限（比照 ``deployment._writer_lock``：LOCK_NB 輪詢到期限）。
# 臨界區只是讀寫一個小 JSON，正常情況下毫秒級；等不到就放棄這次更新
# （fail-open），不能讓建議性狀態拖住 routing。
LOCK_TIMEOUT_SECONDS = 2.0
_LOCK_POLL_SECONDS = 0.02
# 取鎖逾時一次之後，同一個 store 在這段時間內直接放棄更新，不再每次等滿期限。
LOCK_FAILURE_COOLDOWN_SECONDS = 60.0
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

    狀態依 task class 分開記錄（``task_classes.<task_class>.<profile_id>``）：
    同一個 profile 的確定性失敗可能只在某類 prompt 出現（cg 的
    ``Invalid command format`` 只在 prompt 以 ``-`` 開頭時發生），共用一筆狀態
    會讓 title 的成功清掉 atomization 的退避、兩邊來回翻轉。

    寫入是「取鎖→重讀→修改→原子替換」：dream、直呼 ``hippo atomize``、hook
    裡的 title importer、skillopt 可能同時更新，所以凡是可能改變狀態的判斷
    都在 ``profile-health.json.lock`` 的 ``flock`` 內重讀後才決定（鎖檔是
    flock rendezvous inode，永不 unlink）；暫存檔名每次唯一（比照
    ``moc.search._unique_tmp``），交錯的 writer 不會互刪對方的暫存檔。

    取鎖有期限（``LOCK_EX|LOCK_NB`` 輪詢，比照 ``deployment._writer_lock``）：
    等不到就放棄這次更新並記一行 warning，routing 照常進行；逾時後
    ``LOCK_FAILURE_COOLDOWN_SECONDS`` 內同一個 store 直接放棄更新，不會每個
    attempt 都等滿期限。讀取不取鎖、不會被卡住：檔案只以 ``os.replace`` 整檔
    替換，讀到的永遠是某一版完整內容。``read_only=True``（dry-run、doctor）
    時只讀不寫、也不取鎖。
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] | None = None,
        read_only: bool = False,
        lock_timeout: float = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._path = Path(path)
        self._clock = clock or time.time
        self._read_only = read_only
        self._lock_timeout = float(lock_timeout)
        self._writes_suspended_until = 0.0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def lock_path(self) -> Path:
        return self._path.with_name(f"{self._path.name}.lock")

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("schema_version") != HEALTH_SCHEMA_VERSION:
            return {}
        task_classes = data.get("task_classes")
        if not isinstance(task_classes, dict):
            return {}
        loaded: dict[str, dict[str, dict[str, Any]]] = {}
        for task_class, profiles in task_classes.items():
            if not isinstance(profiles, dict):
                continue
            entries = {
                str(key): dict(value)
                for key, value in profiles.items()
                if isinstance(value, dict)
            }
            if entries:
                loaded[str(task_class)] = entries
        return loaded

    def _acquire(self, handle: Any) -> bool:
        """有期限地取得寫入鎖；逾時回 False（其他 OSError 上拋給呼叫端記錄）。"""
        deadline = _monotonic() + self._lock_timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if _monotonic() >= deadline:
                    return False
                _sleep(_LOCK_POLL_SECONDS)

    def _unique_tmp(self) -> Path:
        return self._path.with_name(
            f".{self._path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp"
        )

    def _save(self, state: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> None:
        payload = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "task_classes": {
                task_class: {key: dict(profiles[key]) for key in sorted(profiles)}
                for task_class, profiles in sorted(state.items())
                if profiles
            },
        }
        tmp = self._unique_tmp()
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            _LOG.warning("agent health: cannot persist %s: %s", self._path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass

    def _update(
        self, mutate: Callable[[dict[str, dict[str, dict[str, Any]]]], bool]
    ) -> None:
        """在寫入鎖內重新讀檔、套用 ``mutate``，有變更才寫回。

        取不到鎖（逾時）或任何 I/O 錯誤都只放棄這次更新（fail-open）。
        """
        if self._read_only:
            return
        if _monotonic() < self._writes_suspended_until:
            _LOG.debug("agent health: updates suspended after a lock timeout; skipped")
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                if not self._acquire(handle):
                    self._writes_suspended_until = (
                        _monotonic() + LOCK_FAILURE_COOLDOWN_SECONDS
                    )
                    _LOG.warning(
                        "agent health: %s still locked after %.1fs; skipping this "
                        "update (fail-open), further updates suspended for %.0fs",
                        self.lock_path, self._lock_timeout, LOCK_FAILURE_COOLDOWN_SECONDS,
                    )
                    return
                try:
                    state = self._load()
                    if mutate(state):
                        self._save(state)
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            _LOG.warning("agent health: cannot update %s: %s", self._path, exc)

    def entries(self) -> dict[str, dict[str, dict[str, Any]]]:
        """``{task_class: {profile_id: entry}}``。"""
        return self._load()

    @staticmethod
    def _applies(entry: Mapping[str, Any], profile: AgentProfile) -> bool:
        # command 變了（例如 operator 修好 argv）→ 舊狀態不再套用。
        return entry.get("command_fingerprint") == profile.command_fingerprint()

    def blocked_reason(
        self, profile: AgentProfile, task_class: str = "atomization"
    ) -> str | None:
        entry = self._load().get(task_class, {}).get(profile.id)
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

    def record_outcome(
        self,
        profile: AgentProfile,
        result: AgentRunResult,
        task_class: str = "atomization",
    ) -> None:
        # 唯一不取鎖的快速路徑：唯讀 store，或 router 自己的判斷（budget／
        # ineligible）。兩者都與檔案內容無關、一定不寫入，因此沒有競態。
        # 其餘結果（包括「成功且看起來沒有狀態」）都必須在鎖內重讀再決定：
        # 鎖外讀到的「沒有狀態」可能在返回前就被並行的失敗寫入推翻。
        if self._read_only or result.failure_category in _NO_SIGNAL_CATEGORIES:
            return
        kind = persistent_failure_kind(result)

        def mutate(state: dict[str, dict[str, dict[str, Any]]]) -> bool:
            profiles = state.setdefault(task_class, {})
            if kind is None:
                # 成功，或是非確定性失敗：代表確定性狀況沒有重現，清掉狀態。
                return profiles.pop(profile.id, None) is not None
            now = float(self._clock())
            previous = profiles.get(profile.id)
            if previous and previous.get("kind") == kind and self._applies(previous, profile):
                failures = int(previous.get("consecutive_failures", 0) or 0) + 1
                first_failed_at = str(previous.get("first_failed_at") or _utc(now))
            else:
                failures = 1
                first_failed_at = _utc(now)
            threshold = int(PERSISTENT_FAILURE_THRESHOLDS.get(kind, 1))
            blocked_until: float | None = None
            if failures >= threshold:
                exponent = min(failures - threshold, 16)
                blocked_until = now + min(
                    BACKOFF_BASE_SECONDS * (2 ** exponent), BACKOFF_CAP_SECONDS
                )
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
            return True

        self._update(mutate)

    def _describe_entry(self, entry: Mapping[str, Any], profile: AgentProfile) -> str:
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
            state = f"probe-pending({kind})：退避已到期，下一次呼叫會再試一次 failures={failures}"
        else:
            state = f"degraded({kind}) failures={failures}/{threshold}"
        suffix = f' last="{detail}"' if detail else ""
        return f"{state}{suffix}"

    def describe(self, profile: AgentProfile) -> str | None:
        """``hippo doctor`` 用的一行摘要（逐 task class）；沒有狀態時回 None。"""
        state = self._load()
        parts = [
            f"health[{task_class}]={self._describe_entry(profiles[profile.id], profile)}"
            for task_class, profiles in sorted(state.items())
            if profile.id in profiles
        ]
        return " ".join(parts) if parts else None


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
