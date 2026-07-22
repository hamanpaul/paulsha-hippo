"""
Processing ledger: session state machine for Stage 2 T3 atomizer/linker.

Deterministic state tracking for split/promoted lifecycle.
Canonical JSON: json.dumps(v, sort_keys=True, separators=(",", ":"))
"""
import fcntl
import json
import os
from pathlib import Path
from typing import Any


VALID_STATES = {"split", "promoted", "no-findings", "skipped", "parked", "quarantined"}
PARKED_FAILURE_CATEGORIES = {
    "backend_unavailable",
    "transient",
    "invalid_output",
    "context_budget_exceeded",
}
_ERROR_TEXT_MAX_CHARS = 500
_REDACTION_FAILED_PLACEHOLDER = "[REDACTION UNAVAILABLE: text withheld]"


def redact_secret_text(text: str) -> str:
    """套用 baseline policy secret redaction 規則（fail-closed、不可被 override 弱化）。

    命中 credential（GitHub PAT／Bearer token／OpenAI・Anthropic key 等
    `policy/secrets.yaml` 規則）的行整行以 `[REDACTED LINE: <rule> xN]` 佔位
    （沿用 `policy.redact_lines` 既有語意）。redaction 機制本身失效（policy
    載入／regex 錯誤）時整段以 placeholder 取代——秘密永不落 ledger／evidence。

    Codex 複驗 blocking：這裡是持久化出口（parked evidence、processing ledger、
    dream ledger）的強制 scrub，必須以 `override_path=None` 載入 immutable
    baseline 規則——使用者 policy.override.yaml 的 `disable_rules`／
    `disable_rules_for_session` 是給蒸餾管線調誤判用的，不得停用持久化 sanitize，
    否則 credential 原文直落 `_failed/*.json`／processing.jsonl／dream.jsonl。
    """
    try:
        from paulsha_hippo.policy import load_policy, redact_lines

        return redact_lines(
            str(text),
            policy=load_policy(override_path=None),
            session_ref=None,
            boundary="raw_to_distilled",
        ).text
    except Exception:  # noqa: BLE001 —fail-closed：任何失敗都不得回傳原文
        return _REDACTION_FAILED_PLACEHOLDER


def sanitize_error_text(text: str, limit: int = _ERROR_TEXT_MAX_CHARS) -> str:
    """Bounded、去敏的錯誤文字：壓平 whitespace、遮蔽 home 前綴、secret
    redaction（fail-closed）、截斷。

    parked 事件與 dream orchestrator 的 error 欄位共用（契約：≤500 字元、去敏、
    無 credential）。redaction 必須先於截斷：先截斷可能把 token 斬半、令 pattern
    失配而留下敏感前綴。
    """
    collapsed = " ".join(str(text).split())
    home = str(Path.home())
    if home and home != "/":
        collapsed = collapsed.replace(home, "~")
    return redact_secret_text(collapsed)[:limit]


class ProcessingLedgerError(Exception):
    """Raised when processing ledger is corrupt or invalid."""
    pass


def processing_path(memory_root: Path) -> Path:
    """Return path to processing.jsonl ledger."""
    return memory_root / "runtime" / "ledger" / "processing.jsonl"


def append_state(
    memory_root: Path,
    *,
    session_key: str,
    state: str,
    now: str,
    config_hash: str,
    **extra: Any
) -> None:
    """
    Append a state transition event to the processing ledger.
    
    Args:
        memory_root: Root path for memory storage
        session_key: Session identifier (e.g., "claude:s1")
        state: State value (must be in VALID_STATES)
        now: ISO timestamp string (injected for determinism)
        config_hash: Hash of atomizer configuration
        **extra: Additional fields to include in event
    
    Raises:
        ValueError: If state is not valid
    """
    if state not in VALID_STATES:
        raise ValueError(f"invalid processing state: {state}")
    if state == "parked" and extra.get("failure_category") not in PARKED_FAILURE_CATEGORIES:
        raise ValueError(
            f"parked event requires failure_category in {sorted(PARKED_FAILURE_CATEGORIES)}"
        )

    ledger_path = processing_path(memory_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build event with canonical field order
    event = {
        "ts": now,
        "session_key": session_key,
        "state": state,
        "atomizer_config_hash": config_hash,
        **extra
    }
    
    # Canonical JSON: sorted keys, compact separators
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    
    # Append with exclusive lock
    with open(ledger_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _parse_events(lines: Any) -> list[dict[str, Any]]:
    """Parse JSONL lines into events (blank lines skipped).

    Shared by read_events 與 transition_state_atomic 持鎖時的重讀，確保兩者
    從「同一套解析」摺疊，原子轉移的守門判定與 fold_events 的勝出判定同源。

    Raises:
        ProcessingLedgerError: If a line is malformed JSON.
    """
    events = []
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:  # Skip blank lines
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ProcessingLedgerError(
                f"Malformed JSON at line {line_num}: {e}"
            ) from e
    return events


def read_events(memory_root: Path) -> list[dict[str, Any]]:
    """
    Read all events from processing ledger.

    Args:
        memory_root: Root path for memory storage

    Returns:
        List of event dictionaries in file order

    Raises:
        ProcessingLedgerError: If ledger contains malformed JSON
    """
    ledger_path = processing_path(memory_root)

    if not ledger_path.exists():
        return []

    with open(ledger_path, "r") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return _parse_events(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _fold_indexed(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold events into latest-per-session map by (ts, original_index).

    Latest timestamp wins deterministically；ts 相同時後追加者（index 較大）勝出。
    抽為純函式讓 transition_state_atomic 能在持鎖重讀後，用與 fold_events 完全
    相同的勝出規則預判「這次 append 是否真的會成為最新狀態」。
    """
    indexed_events = [(event, idx) for idx, event in enumerate(events)]
    indexed_events.sort(key=lambda x: (x[0].get("ts", ""), x[1]))

    event_map: dict[str, dict[str, Any]] = {}
    for event, _ in indexed_events:
        session_key = event.get("session_key")
        if session_key:
            event_map[session_key] = event
    return event_map


def fold_states(memory_root: Path) -> dict[str, str]:
    return {
        session_key: str(event["state"])
        for session_key, event in fold_events(memory_root).items()
        if event.get("state")
    }


def fold_events(memory_root: Path) -> dict[str, dict[str, Any]]:
    """
    Fold events into latest-event map.

    Events are sorted by (ts, original_index) so latest timestamp wins
    deterministically if timestamps differ, otherwise file order wins.

    Args:
        memory_root: Root path for memory storage

    Returns:
        Dictionary mapping session_key to the latest event for that session
    """
    return _fold_indexed(read_events(memory_root))


def transition_state_atomic(
    memory_root: Path,
    *,
    session_key: str,
    expected_states: Any,
    state: str,
    now: str,
    config_hash: str,
    **extra: Any,
) -> tuple[bool, str]:
    """條件式狀態轉移：整段 read-check-write 持同一把 exclusive lock，原子提交。

    `append_state` 只在寫入瞬間持鎖，呼叫端拿的是進入時的 fold 快照——parked→split
    這類「先驗證再改寫」的轉移因此有 read-check-write race：快照後另一 writer 若已
    完成 promoted/parked，過期的 append 仍會把狀態改回去；而 fold_events 依事件 ts
    （非追加順序）定最新狀態，帶較舊 `now` 的呼叫更會讓 append 落地卻不是勝出事件，
    回報成功但實際狀態未變（false-success）。

    本函式在 `LOCK_EX` 內「重讀 ledger、重新 fold」後才判定（一律以持鎖重讀為準，
    不信任呼叫端快照）：

      1. 目前 fold 後狀態必須 ∈ `expected_states`，否則拒絕，reason 回傳目前狀態字串
         （session 無事件則 `"unknown session"`）——與 requeue 既有 non-parked skip
         語意一致。攔下「快照後被 promote/park」造成的錯誤復活。
      2. `now` 不得早於該 session 目前最新事件的 ts；較舊 ts 不會贏得 ts 排序的 fold，
         append 會被既有事件遮蔽、狀態實際不變——拒絕，reason `"stale-timestamp"`。

    通過才在持鎖期間 append（fsync），並「再次重讀 fold」確認勝出事件狀態確為
    `state` 才回 `(True, "")`；殘留不符則回 `(False, "fold-verify-failed")`，永不把
    未生效的轉移回報成功。任何拒絕分支都不寫入。

    驗證同 `append_state`（target state 合法；parked 需已知 failure_category）。
    """
    if state not in VALID_STATES:
        raise ValueError(f"invalid processing state: {state}")
    if state == "parked" and extra.get("failure_category") not in PARKED_FAILURE_CATEGORIES:
        raise ValueError(
            f"parked event requires failure_category in {sorted(PARKED_FAILURE_CATEGORIES)}"
        )

    ledger_path = processing_path(memory_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "ts": now,
        "session_key": session_key,
        "state": state,
        "atomizer_config_hash": config_hash,
        **extra,
    }
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    expected = set(expected_states)

    # a+：可讀可（append 模式）寫；O_APPEND 保證寫入一律落在 EOF，seek 不影響寫位置。
    with open(ledger_path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            current = _fold_indexed(_parse_events(f)).get(session_key)
            current_state = str(current.get("state", "")) if current else ""
            if current_state not in expected:
                return False, current_state or "unknown session"
            latest_ts = str(current.get("ts", "")) if current else ""
            if now < latest_ts:
                return False, "stale-timestamp"

            f.seek(0, os.SEEK_END)  # 滿足 stdio read→write 交錯需先 seek 的規則
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

            # 持同一把鎖重讀，確認 append 後的勝出事件狀態確為 target——
            # 永不把「落地但被遮蔽」的轉移回報成功。
            f.seek(0)
            after = _fold_indexed(_parse_events(f)).get(session_key)
            if not after or str(after.get("state", "")) != state:
                return False, "fold-verify-failed"
            return True, ""
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def state_of(memory_root: Path, session_key: str) -> str | None:
    """
    Get current state for a session.
    
    Args:
        memory_root: Root path for memory storage
        session_key: Session identifier
    
    Returns:
        Current state string, or None if session not found
    """
    states = fold_states(memory_root)
    return states.get(session_key)
