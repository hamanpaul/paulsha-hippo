## MODIFIED Requirements

### Requirement: Idle-gated scheduling

Stage 2 SHALL ship a systemd user unit/timer template and an idle-check wrapper so the dream service can run on a recurring schedule only when the system is idle. The timer template MUST use `OnCalendar` on a recurring schedule (the concrete cadence is governed by the `dream-resource-governance` capability) and MUST invoke `dream run --require-idle`. `--require-idle` MUST skip the run (log and exit zero) when the system is confirmed busy, and MUST proceed when idle or when idleness cannot be determined (fail-safe-to-run). The idle decision MUST be implemented in Python so it is unit-testable with an injected probe.

#### Scenario: Busy system skips

- **WHEN** `dream run --require-idle` runs and the idle probe reports busy
- **THEN** the run MUST be skipped, exit zero, and write no `dream.jsonl` record

#### Scenario: Indeterminate idleness proceeds

- **WHEN** the idle probe cannot determine load
- **THEN** the run MUST proceed
