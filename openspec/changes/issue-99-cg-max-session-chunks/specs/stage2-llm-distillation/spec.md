## MODIFIED Requirements

### Requirement: Deterministic tiered fallback

The default ordered groups SHALL be Tier 1 `claude` and `codex` as difficult-decision judges, Tier 2 `agy` and `cg` as fast-response/heavy-work agents, and Tier 3 `co-gem`, `claude-gem`, and custom local profiles as low-cost fallback. Exact order within a tier SHALL use explicit numeric priority. Traits SHALL be reviewable routing metadata, not free-form instructions that permit the model to choose its successor. The fallback graph SHALL be acyclic and constrained by a global deadline, maximum attempts, maximum agent calls, and per-profile circuit breaker/cooldown. Dream profiles SHALL disable CLI-native model fallback/retry; a CLI for which preflight cannot prove native fallback is disabled SHALL be ineligible, so Hippo remains the sole routing and budget authority.

The global deadline SHALL scale with the number of prompt chunks the session was packed into, bounded by a fixed floor and a fixed ceiling, so that a session's chain budget bears a defined relationship to the work it contains. The per-call timeout SHALL remain a separate fixed bound and MUST NOT be widened by this scaling; hang protection is not negotiable. A profile MAY declare a maximum session size it is suited for; when a session exceeds that declaration the profile SHALL be ineligible for that session, SHALL be recorded in attempt provenance, and MUST NOT consume an agent call.

#### Scenario: Oversized session skips cg profile without cost
- **WHEN** a session's chunk count exceeds cg profile's declared maximum session size (6 chunks)
- **THEN** cg profile SHALL be ineligible for the session, SHALL appear in attempt provenance, and SHALL consume no agent call
