from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import time

from .audit import append_policy_audits, build_policy_audit_events
from .classification import ClassificationResult, classify_artifact
from .loader import load_policy
from .models import EffectivePolicy, PolicyExecutionError
from .redaction import CompletedGitleaks, PolicyHit, redact_lines, run_gitleaks

DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_BACKOFF_MS = 50
MVP_EXECUTABLE_BOUNDARIES = frozenset({"external_to_raw", "raw_to_distilled"})


@dataclass(frozen=True)
class BoundaryResult:
    text: str
    hits: tuple[PolicyHit, ...]
    classification: ClassificationResult
    ledger_metadata: dict[str, object]
    policy: EffectivePolicy


@dataclass(frozen=True)
class QueuePolicyResult:
    status: str
    stub_path: Path | None = None
    inbox_path: Path | None = None
    boundary_result: BoundaryResult | None = None


def check_boundary(
    boundary: str,
    text: str,
    *,
    project_slug: str,
    session_ref: str,
    policy: EffectivePolicy | None = None,
    gitleaks_runner=None,
) -> BoundaryResult:
    effective_policy = policy if policy is not None else load_policy()
    _ensure_mvp_executable_boundary(effective_policy, boundary)
    extra_hits: tuple[PolicyHit, ...] = ()
    if boundary == "raw_to_distilled":
        runner_kwargs = {}
        if gitleaks_runner is not None:
            runner_kwargs["runner"] = gitleaks_runner
        extra_hits = run_gitleaks(text, **runner_kwargs)

    redaction = redact_lines(
        text,
        policy=effective_policy,
        session_ref=session_ref,
        boundary=boundary,
        extra_hits=extra_hits,
    )
    classification = classify_artifact(
        policy=effective_policy,
        project_slug=project_slug,
        redaction_hits=redaction.hits,
    )
    ledger_metadata = {
        "redaction_hits": redaction.hit_count,
        "redaction_types": sorted({hit.rule_id for hit in redaction.hits}),
        "redaction_stage": redaction.stage,
        "policy_version": effective_policy.policy_version,
        "effective_policy_hash": effective_policy.effective_policy_hash,
        "classification_level": classification.level,
        "classification_reason": classification.reason,
        "classification_policy_hash": classification.policy_hash,
        "classification_source": classification.source,
    }
    return BoundaryResult(redaction.text, redaction.hits, classification, ledger_metadata, effective_policy)


def write_failure_stub(
    failed_dir: str | Path,
    *,
    session_ref: str,
    source_tool: str,
    boundary: str,
    error_class: str,
    policy_version: str | None,
    effective_policy_hash: str | None,
    ledger_available: bool,
) -> Path:
    failed_path = Path(failed_dir)
    failed_path.mkdir(parents=True, exist_ok=True)
    stub_path = failed_path / f"{_safe_name(session_ref)}-{_safe_name(boundary)}-policy-error.json"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_ref": session_ref,
        "source_tool": source_tool,
        "boundary": boundary,
        "error_class": error_class,
        "policy_version": policy_version,
        "effective_policy_hash": effective_policy_hash,
        "ledger_status": "available" if ledger_available else "unavailable",
    }
    stub_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return stub_path


def handle_policy_failure(
    *,
    queue_path: str | Path,
    failed_dir: str | Path,
    inbox_path: str | Path,
    session_ref: str,
    source_tool: str,
    boundary: str,
    error: Exception,
    policy: EffectivePolicy | None,
    ledger_available: bool,
) -> QueuePolicyResult:
    queue = Path(queue_path)
    inbox = Path(inbox_path)
    _unlink_file_if_present(inbox)
    _unlink_file_if_present(queue)
    stub = write_failure_stub(
        failed_dir,
        session_ref=session_ref,
        source_tool=source_tool,
        boundary=boundary,
        error_class=error.__class__.__name__,
        policy_version=policy.policy_version if policy else None,
        effective_policy_hash=policy.effective_policy_hash if policy else None,
        ledger_available=ledger_available,
    )
    return QueuePolicyResult(status="policy-error", stub_path=stub)


def process_queue_with_policy(
    *,
    queue_path: str | Path,
    inbox_path: str | Path,
    failed_dir: str | Path,
    boundary: str,
    project_slug: str,
    session_ref: str,
    source_tool: str,
    gitleaks_runner=None,
    policy: EffectivePolicy | None = None,
    ledger_available: bool = True,
    audit_path: str | Path | None = None,
) -> QueuePolicyResult:
    queue = Path(queue_path)
    inbox = Path(inbox_path)
    if policy is not None and boundary in policy.boundaries:
        retry_count = policy.boundaries[boundary].retry_count
        retry_backoff_ms = policy.boundaries[boundary].retry_backoff_ms
    else:
        retry_count = DEFAULT_RETRY_COUNT
        retry_backoff_ms = DEFAULT_RETRY_BACKOFF_MS
    last_error: Exception = PolicyExecutionError("policy check did not run")
    effective_policy: EffectivePolicy | None = policy
    attempt = 0
    while attempt < retry_count:
        try:
            effective_policy = policy if policy is not None else load_policy()
            if boundary not in effective_policy.boundaries:
                raise PolicyExecutionError(f"unknown boundary: {boundary}")
            boundary_policy = effective_policy.boundaries[boundary]
            retry_count = boundary_policy.retry_count
            retry_backoff_ms = boundary_policy.retry_backoff_ms
            text = queue.read_text(encoding="utf-8")
            result = check_boundary(
                boundary,
                text,
                project_slug=project_slug,
                session_ref=session_ref,
                policy=effective_policy,
                gitleaks_runner=gitleaks_runner,
            )
        except Exception as exc:
            last_error = exc
            attempt += 1
            if attempt < retry_count and retry_backoff_ms > 0:
                time.sleep(retry_backoff_ms / 1000)
            continue
        try:
            _append_boundary_audit_records(
                audit_path=audit_path,
                boundary=boundary,
                session_ref=session_ref,
                policy=effective_policy,
                result=result,
            )
            _write_text_atomically(inbox, result.text)
            queue.unlink()
        except OSError as exc:
            return handle_policy_failure(
                queue_path=queue,
                failed_dir=failed_dir,
                inbox_path=inbox,
                session_ref=session_ref,
                source_tool=source_tool,
                boundary=boundary,
                error=PolicyExecutionError(f"publish failed: {exc.__class__.__name__}"),
                policy=effective_policy,
                ledger_available=ledger_available,
            )
        return QueuePolicyResult(status="ok", inbox_path=inbox, boundary_result=result)

    return handle_policy_failure(
        queue_path=queue,
        failed_dir=failed_dir,
        inbox_path=inbox,
        session_ref=session_ref,
        source_tool=source_tool,
        boundary=boundary,
        error=last_error,
        policy=effective_policy,
        ledger_available=ledger_available,
    )


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value)
    return safe or "unknown"


def _ensure_mvp_executable_boundary(policy: EffectivePolicy, boundary: str) -> None:
    boundary_policy = policy.boundaries.get(boundary)
    if boundary_policy is None:
        raise PolicyExecutionError(f"unknown boundary: {boundary}")
    if boundary_policy.status == "deferred" or boundary not in MVP_EXECUTABLE_BOUNDARIES:
        raise PolicyExecutionError(f"deferred boundary not executable in MVP: {boundary}")


def _unlink_file_if_present(path: Path) -> None:
    if path.exists() and (path.is_file() or path.is_symlink()):
        path.unlink()


def _append_boundary_audit_records(
    *,
    audit_path: str | Path | None,
    boundary: str,
    session_ref: str,
    policy: EffectivePolicy | None,
    result: BoundaryResult,
) -> None:
    if audit_path is None or policy is None:
        return
    boundary_policy = policy.boundaries.get(boundary)
    if boundary_policy is None or not boundary_policy.audit_required:
        return
    append_policy_audits(
        audit_path,
        build_policy_audit_events(
            boundary=boundary,
            component=str(result.ledger_metadata["redaction_stage"]),
            session_ref=session_ref,
            policy=policy,
            hits=result.hits,
        ),
    )


def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.tmp-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            tmp_path = Path(handle.name)
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            _unlink_file_if_present(tmp_path)
        raise
