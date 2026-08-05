## ADDED Requirements

### Requirement: Skipped-profile provenance on chain-deadline exhaustion

When the session chain deadline expires before the router has reached every enabled profile whose task classes match the session, the router SHALL append one attempt record for each remaining enabled, task-class-matching profile before breaking, using the existing ineligible provenance style: `failure_category="ineligible"`, a `session_deadline` reason, zero elapsed time, and no agent call consumed. Skipped-profile records MUST NOT alter dispatch order, deadline arithmetic, fallback semantics, or park behavior, and the `session_deadline` reason MUST NOT be added to the fallback-category allowlist; profiles already skipped by an open circuit breaker SHALL keep their existing recording behavior unchanged. The ordered attempt chain handed to parking SHALL therefore account for every enabled, task-class-matching profile, so a profile can no longer vanish from provenance because the chain budget was exhausted before its eligibility was evaluated.

#### Scenario: Deadline break records the profiles it skipped
- **WHEN** earlier profile attempts consume the entire chain deadline before the router reaches later enabled, task-class-matching profiles
- **THEN** each skipped profile SHALL appear in attempt provenance as `ineligible` with reason `session_deadline`, consuming no agent call, and the session SHALL otherwise park exactly as before

#### Scenario: Sufficient budget leaves provenance unchanged
- **WHEN** the chain deadline is not exhausted and the router walks the full profile chain
- **THEN** attempt records SHALL be identical to the pre-change behavior with no skipped-profile records appended
