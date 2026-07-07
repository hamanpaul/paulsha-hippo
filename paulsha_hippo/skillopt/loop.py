from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

Dataset = list[dict]
Rollout = Callable[[str, Any], Any]
Score = Callable[[Any, Any], float]
Optimizer = Callable[[str, list[dict]], str]


class SkillOptError(Exception):
    """Raised when optimization cannot proceed safely (e.g., empty val set)."""


def _is_valid_skill(text: str) -> bool:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False

    end_idx: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return False

    body_lines = lines[end_idx + 1 :]
    return any(l.strip() for l in body_lines)


def _safe_score(score: Score, output: Any, gold: Any) -> float:
    return float(score(output, gold))


def _mean_score(skill_text: str, dataset: Dataset, rollout: Rollout, score: Score) -> float:
    if not dataset:
        raise SkillOptError("dataset is empty")

    total = 0.0
    for item in dataset:
        output = rollout(skill_text, item["input"])
        total += _safe_score(score, output, item["gold"])
    return total / len(dataset)


def _append_record(record_path: Path | None, record: dict) -> None:
    if record_path is None:
        return
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _record(result: dict[str, Any]) -> dict[str, Any]:
    """Record subset: scores/counts/decision only — never failure content."""

    keys = (
        "accepted",
        "baseline_score",
        "candidate_score",
        "improvement",
        "failures_used",
    )
    return {k: result.get(k) for k in keys}


def _sanitize_error_result(result: dict[str, Any]) -> dict[str, Any]:
    # Ensure error results never retain success-only metadata.
    result["accepted"] = False
    result["reason"] = "error"
    result["baseline_score"] = None
    result["candidate_score"] = None
    result["improvement"] = None
    result["skill_valid"] = False
    result.pop("history_backup", None)
    return result


def _restore_original_skill(skill_path: Path, *, backup_path: Path | None, original_text: str) -> None:
    """Best-effort restore with a rename-first strategy.

    Renaming the already-written backup back into place tends to be more reliable
    than creating a new temp file (e.g., in low-disk scenarios).
    """

    if backup_path is not None:
        try:
            if backup_path.exists():
                backup_path.replace(skill_path)
                return
        except Exception:
            pass

    _atomic_write(skill_path, original_text)


def optimize_skill(
    skill_path: Path,
    *,
    rollout: Rollout,
    score: Score,
    train_set: Dataset,
    val_set: Dataset,
    optimizer: Optimizer,
    budget: int = 1,
    accept_threshold: float = 0.0,
    now: str,
    record_path: Path | None = None,
    failure_count: int = 5,
) -> dict[str, Any]:
    skill_path = Path(skill_path)
    if not val_set:
        raise SkillOptError("val_set must be non-empty")

    result: dict[str, Any] = {
        "accepted": False,
        "ts": now,
        "skill_path": str(skill_path),
        "failures_used": 0,
        "skill_valid": False,
        "baseline_score": None,
        "candidate_score": None,
        "improvement": None,
        "reason": "",
    }

    baseline_score: float

    try:
        current_text = skill_path.read_text(encoding="utf-8")

        baseline_score = _mean_score(current_text, val_set, rollout, score)
        result["baseline_score"] = baseline_score

        best_text = current_text
        best_score = baseline_score
        invalid_candidate = False
        valid_candidate = False

        attempts = max(0, int(budget))
        for _ in range(attempts):
            scored: list[dict[str, Any]] = []
            for item in train_set:
                output = rollout(best_text, item["input"])
                scored.append(
                    {
                        "id": item["id"],
                        "input": item["input"],
                        "output": output,
                        "gold": item["gold"],
                        "score": _safe_score(score, output, item["gold"]),
                    }
                )

            failures = sorted(scored, key=lambda f: f["score"])[:failure_count]
            result["failures_used"] = len(failures)

            candidate_text = optimizer(best_text, failures)
            if not _is_valid_skill(candidate_text):
                invalid_candidate = True
                break

            valid_candidate = True
            candidate_score = _mean_score(candidate_text, val_set, rollout, score)
            if candidate_score > best_score:
                best_text = candidate_text
                best_score = candidate_score

        # Candidate validity is orthogonal to accept/reject.
        result["skill_valid"] = valid_candidate

        accepted = best_score > baseline_score + accept_threshold
        result["accepted"] = accepted

        skill_written = False
        backup_path: Path | None = None

        if accepted:

            ts_safe = now.replace(":", "-")
            backup_path = (
                skill_path.parent
                / "skillopt-history"
                / skill_path.stem
                / f"{ts_safe}.md"
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(current_text, encoding="utf-8")
            _atomic_write(skill_path, best_text)
            skill_written = True

            result.update(
                {
                    "candidate_score": best_score,
                    "improvement": best_score - baseline_score,
                    "reason": "accepted",
                    "history_backup": str(backup_path),
                }
            )
        else:
            reason = "rejected: no improvement"
            if invalid_candidate and best_score == baseline_score:
                result["skill_valid"] = False
                reason = "rejected: invalid skill"

            result.update(
                {
                    "candidate_score": best_score,
                    "improvement": best_score - baseline_score,
                    "reason": reason,
                }
            )

        try:
            _append_record(record_path, _record(result))
        except Exception:
            # Fail-closed: never leave the skill mutated without a durable record.
            rollback_failed = False
            if accepted and skill_written:
                try:
                    _restore_original_skill(
                        skill_path, backup_path=backup_path, original_text=current_text
                    )
                except Exception:
                    rollback_failed = True

            _sanitize_error_result(result)
            if rollback_failed:
                result["rollback_failed"] = True
            return result

        return result

    except SkillOptError:
        raise
    except Exception:
        # Fail-closed: don't return or record partial scores on unexpected errors.
        _sanitize_error_result(result)
        try:
            _append_record(record_path, _record(result))
        except Exception:
            pass
        return result
