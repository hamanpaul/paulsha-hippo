from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Iterable

from .loader import is_rule_disabled
from .models import EffectivePolicy, PolicyError, PolicyExecutionError


@dataclass(frozen=True)
class PolicyHit:
    rule_id: str
    detector: str
    line_no: int
    action: str


@dataclass(frozen=True)
class RedactionResult:
    text: str
    hits: tuple[PolicyHit, ...]
    stage: str
    effective_policy_hash: str

    @property
    def hit_count(self) -> int:
        return len(self.hits)


@dataclass(frozen=True)
class CompletedGitleaks:
    returncode: int
    stdout: str
    stderr: str


def parse_gitleaks_report(report_text: str) -> tuple[PolicyHit, ...]:
    if not report_text.strip():
        return ()
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as exc:
        raise PolicyExecutionError("invalid gitleaks JSON report") from exc
    if not isinstance(report, list):
        raise PolicyExecutionError("gitleaks report must be a JSON list")

    hits: list[PolicyHit] = []
    for item in report:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("RuleID", "gitleaks"))
        start_line = int(item.get("StartLine", 0))
        end_line = int(item.get("EndLine", start_line))
        if start_line > 0:
            for line_no in range(start_line, max(start_line, end_line) + 1):
                hits.append(PolicyHit(rule_id, "gitleaks", line_no, "redact"))
    return tuple(hits)


def run_gitleaks(
    text: str,
    *,
    binary: str = "gitleaks",
    runner=subprocess.run,
) -> tuple[PolicyHit, ...]:
    try:
        with TemporaryDirectory(prefix="policy-gitleaks-") as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "input.txt"
            report = tmp_dir / "report.json"
            source.write_text(text, encoding="utf-8")
            completed = runner(
                [
                    binary,
                    "detect",
                    "--no-git",
                    "--source",
                    str(source),
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            returncode = int(getattr(completed, "returncode", 0))
            stdout = str(getattr(completed, "stdout", ""))
            if returncode not in (0, 1):
                raise PolicyExecutionError(f"gitleaks failed with exit code {returncode}")
            report_text = report.read_text(encoding="utf-8") if report.exists() else stdout
    except FileNotFoundError as exc:
        raise PolicyExecutionError("gitleaks binary not found") from exc
    except OSError as exc:
        raise PolicyExecutionError(f"gitleaks execution failed: {exc}") from exc
    return parse_gitleaks_report(report_text)


def redact_lines(
    text: str,
    *,
    policy: EffectivePolicy,
    session_ref: str | None,
    boundary: str,
    extra_hits: Iterable[PolicyHit] = (),
) -> RedactionResult:
    lines = text.splitlines(keepends=True)
    hits = list(_regex_hits(lines, policy=policy, session_ref=session_ref))
    hits.extend(extra_hits)

    hits_by_line: dict[int, list[PolicyHit]] = defaultdict(list)
    for hit in hits:
        hits_by_line[hit.line_no].append(hit)

    redacted_lines: list[str] = []
    for line_no, line in enumerate(lines, start=1):
        line_hits = hits_by_line.get(line_no)
        if not line_hits:
            redacted_lines.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        redacted_lines.append(f"[REDACTED LINE: {_placeholder_summary(line_hits)}]{newline}")

    ordered_hits = tuple(sorted(hits, key=lambda hit: (hit.line_no, hit.detector, hit.rule_id)))
    return RedactionResult(
        text="".join(redacted_lines),
        hits=ordered_hits,
        stage=_stage(boundary, ordered_hits),
        effective_policy_hash=policy.effective_policy_hash,
    )


def _regex_hits(
    lines: list[str],
    *,
    policy: EffectivePolicy,
    session_ref: str | None,
) -> tuple[PolicyHit, ...]:
    hits: list[PolicyHit] = []
    compiled_rules = _compiled_regex_rules(policy, session_ref)
    for line_no, line in enumerate(lines, start=1):
        candidates: list[tuple[int, int, PolicyHit]] = []
        for rule_id, detector, pattern in compiled_rules:
            for match in pattern.finditer(line):
                candidates.append(
                    (match.start(), match.end(), PolicyHit(rule_id, detector, line_no, "redact"))
                )
        hits.extend(_non_overlapping_hits(candidates))
    return tuple(hits)


def _compiled_regex_rules(
    policy: EffectivePolicy,
    session_ref: str | None,
) -> tuple[tuple[str, str, re.Pattern[str]], ...]:
    compiled: list[tuple[str, str, re.Pattern[str]]] = []
    for rule in policy.secret_rules.values():
        if rule.detector != "regex" or is_rule_disabled(policy, rule.rule_id, session_ref):
            continue
        try:
            pattern = re.compile(rule.pattern)
        except re.error as exc:
            raise PolicyError(f"invalid regex for rule {rule.rule_id}: {exc}") from exc
        compiled.append((rule.rule_id, rule.detector, pattern))
    return tuple(compiled)


def _non_overlapping_hits(candidates: list[tuple[int, int, PolicyHit]]) -> tuple[PolicyHit, ...]:
    selected: list[tuple[int, int, PolicyHit]] = []
    for candidate in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0], item[2].rule_id)):
        start, end, _hit = candidate
        if any(start < selected_end and selected_start < end for selected_start, selected_end, _ in selected):
            continue
        selected.append(candidate)
    return tuple(hit for _start, _end, hit in sorted(selected, key=lambda item: (item[0], item[2].rule_id)))


def _placeholder_summary(hits: Iterable[PolicyHit]) -> str:
    counts = Counter(hit.rule_id for hit in hits)
    return ", ".join(f"{rule_id} x{count}" for rule_id, count in sorted(counts.items()))


def _stage(boundary: str, hits: tuple[PolicyHit, ...]) -> str:
    if boundary == "external_to_raw":
        return "hook"
    detectors = {hit.detector for hit in hits}
    if "regex" in detectors and "gitleaks" in detectors:
        return "both"
    if "gitleaks" in detectors or boundary == "raw_to_distilled":
        return "importer"
    return "hook"
