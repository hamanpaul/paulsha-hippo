## ADDED Requirements

### Requirement: Skipped-profile provenance on chain-budget exhaustion

When the session chain budget runs out before the router has reached every enabled profile whose task classes match the session — whether the deadline expires at the pre-attempt check (reason `session_deadline`) or the deadline / agent-call budget is exhausted mid-attempt so the chunk loop raises the non-fallback `budget` category (reason `session_budget`) — the router SHALL append one attempt record for each remaining enabled, task-class-matching profile before breaking, using the existing ineligible provenance style: `failure_category="ineligible"`, the applicable reason string, zero elapsed time, and no agent call consumed. A remaining profile whose circuit breaker is currently open SHALL NOT receive a skip record, because the main loop would have silently skipped it even with budget to spare; recording it as budget-skipped would misstate provenance. Skipped-profile records MUST NOT alter dispatch order, deadline arithmetic, fallback semantics, or park behavior, and neither reason string may be added to the fallback-category allowlist. In particular, the exhausted-chain error raised for parking SHALL take its `category`, `profile_id`, `exit_code`, and `stderr` from the terminal attempt that actually ran (or was recorded by the pre-existing ineligible path), never from an appended skip record. Because skip records are appended only after the loop has already decided to break, the attempts list MAY exceed `max_attempts`; the records are provenance-only, park attempt counts derived from the list length therefore include never-executed profiles, and serialization stays bounded by the existing provenance attempt cap. The ordered attempt chain handed to parking SHALL therefore account for every enabled, task-class-matching, non-circuit-open profile, so a profile can no longer vanish from provenance because the chain budget was exhausted before its eligibility was evaluated.

#### Scenario: Deadline break records the profiles it skipped
- **WHEN** earlier profile attempts consume the entire chain deadline before the router reaches later enabled, task-class-matching profiles
- **THEN** each skipped profile SHALL appear in attempt provenance as `ineligible` with reason `session_deadline`, consuming no agent call, and the session SHALL otherwise park exactly as before — the raised error's `category` / `profile_id` / `stderr` SHALL come from the last profile that actually ran

#### Scenario: Mid-attempt budget exhaustion records the profiles it skipped
- **WHEN** the deadline or agent-call budget runs out inside an attempt (e.g. a multi-chunk session whose earlier chunks consume the whole budget), so the chunk loop raises the non-fallback `budget` category and the router breaks
- **THEN** each remaining enabled, task-class-matching profile SHALL appear in attempt provenance as `ineligible` with reason `session_budget`, consuming no agent call, and the raised error SHALL report the terminal real attempt (`category="budget"`, the failing profile's id)

#### Scenario: Circuit-open remaining profiles keep their silent-skip behavior
- **WHEN** a chain-budget break occurs while a remaining profile's circuit breaker is open
- **THEN** that profile SHALL NOT receive a skip record, matching the main loop's silent skip of circuit-open profiles

#### Scenario: Sufficient budget leaves provenance unchanged
- **WHEN** the chain budget is not exhausted and the router walks the full profile chain
- **THEN** attempt records SHALL be identical to the pre-change behavior with no skipped-profile records appended
