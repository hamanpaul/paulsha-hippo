## MODIFIED Requirements

### Requirement: Deterministic tiered fallback

The default ordered groups SHALL be Tier 1 `claude` and `codex` as difficult-decision judges, Tier 2 `agy` and `cg` as fast-response/heavy-work agents, and Tier 3 `co-gem`, `claude-gem`, and custom local profiles as low-cost fallback. Exact order within a tier SHALL use explicit numeric priority. Traits SHALL be reviewable routing metadata, not free-form instructions that permit the model to choose its successor. The fallback graph SHALL be acyclic and constrained by a global deadline, maximum attempts, maximum agent calls, and per-profile circuit breaker/cooldown. Dream profiles SHALL disable CLI-native model fallback/retry; a CLI for which preflight cannot prove native fallback is disabled SHALL be ineligible, so Hippo remains the sole routing and budget authority.

The global deadline SHALL scale with the number of prompt chunks the session was packed into, bounded by a fixed floor and a fixed ceiling, so that a session's chain budget bears a defined relationship to the work it contains. The per-call timeout SHALL remain a separate fixed bound and MUST NOT be widened by this scaling; hang protection is not negotiable. A profile MAY declare a maximum session size it is suited for; when a session exceeds that declaration the profile SHALL be ineligible for that session, SHALL be recorded in attempt provenance, and MUST NOT consume an agent call.

A valid `no_findings` response SHALL be success and MUST NOT trigger fallback. Allowlisted profile-ineligible, auth, rate-limit, capacity, timeout, transport/process, empty-output, and invalid-output categories MAY advance; deterministic input-contract, policy/config, unsafe, or context-budget failures MUST NOT. Success after one or more failed profiles SHALL be reported as `degraded-success`, retaining every prior failure and the fallback reason. Exhaustion SHALL park the session once.

Chunk outputs that already passed response validation SHALL be retained across profile transitions: the next eligible profile SHALL resume from the first chunk that has no validated output rather than repeating validated work. The prompt sequence SHALL remain frozen and every chunk SHALL still be validated individually. Retention MUST NOT weaken exhaustion semantics: when no profile can complete the remaining chunks the session SHALL still be parked exactly once with no partial publication.

#### Scenario: Primary auth failure falls back deterministically
- **WHEN** the first Tier 1 profile returns a sanitized auth failure and policy allows fallback
- **THEN** the next eligible profile in explicit priority order SHALL continue the session from the first chunk without a validated output and successful output SHALL be marked `degraded-success`

#### Scenario: Chain budget scales with session size
- **WHEN** a session is packed into more chunks than the fixed floor budget can cover
- **THEN** the chain deadline SHALL scale with the chunk count up to the fixed ceiling, and each individual agent call SHALL still be bounded by the unchanged per-call timeout

#### Scenario: Oversized session skips an unsuited profile without cost
- **WHEN** a session's chunk count exceeds a profile's declared maximum session size
- **THEN** that profile SHALL be ineligible for the session, SHALL appear in attempt provenance, and SHALL consume no agent call

#### Scenario: Validated chunks survive a profile transition
- **WHEN** a profile validates the first chunks of a session and then fails on a later chunk
- **THEN** the next eligible profile SHALL be asked only for the chunks that have no validated output, and the validated outputs SHALL be reused rather than regenerated

#### Scenario: Safety failure does not fallback
- **WHEN** a profile attempt detects an input-contract, policy, unsafe, invalid-config, or context-budget failure
- **THEN** the session SHALL fail closed immediately without invoking another agent

#### Scenario: Entire chain is exhausted
- **WHEN** every allowed profile fails within the global budgets or is circuit-open/ineligible
- **THEN** the session SHALL be parked exactly once with the ordered attempt chain and no partial publication

#### Scenario: Native fallback cannot be disabled
- **WHEN** profile preflight cannot prove that the external CLI's own model fallback/retry is disabled
- **THEN** that profile SHALL be ineligible for Dream and MUST NOT consume a session attempt

### Requirement: Profile-bound cache and attempt provenance

Distillation cache identity SHALL include task class/operation, response-schema hash/version, router-contract version, profile ID/revision, tier, requested model, requested effort, rendered command fingerprint, effective config hash, skill hash, and prompt hash. Processing records and atoms SHALL retain the selected profile/tier, attempt index, requested model/effort, observed model when verifiable, model-verification status, elapsed time, sanitized failure category, fallback reason, and command/config/skill/build identities. A cache entry or staged output from one operation, schema, profile, or profile revision MUST NOT satisfy another.

When more than one profile contributed validated chunks to a single session, provenance SHALL identify the producing profile for each chunk, and the session-level record SHALL report `degraded-success`. A session completed by multiple profiles MUST NOT be recorded as the product of a single profile. Routing declarations that change which profile is eligible for a session SHALL participate in cache-namespace identity so that a routing change cannot replay outputs produced under different routing, while leaving the rendered command fingerprint unchanged when the command itself is unchanged.

#### Scenario: Agent configuration change invalidates cache
- **WHEN** task class, response schema, router contract, profile, model, effort, command template, config, skill, or prompt changes
- **THEN** the previous cache entry SHALL not be reused and provenance SHALL identify the new request independently

#### Scenario: Mixed-profile session records a producer per chunk
- **WHEN** one profile validates some chunks of a session and another profile validates the rest
- **THEN** provenance SHALL record which profile produced each chunk and the session SHALL be reported as `degraded-success`
