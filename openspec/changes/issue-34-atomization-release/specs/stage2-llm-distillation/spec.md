## MODIFIED Requirements

### Requirement: Per-session LLM atomic distillation

When the atomizer runs with `promoter: llm`, the system SHALL distill one session's fragments into one or more knowledge atoms via the canonical configured backend, allowing cross-fragment merge and single-fragment split, and SHALL attach per-batch `relates_to` and `mentions` relations and `tags`. Promotion MUST be per-session logically all-or-nothing: every produced atom MUST pass the semantic, title, project, provenance, and `slice_frontmatter.validate` gates before publication begins. Publication SHALL use a recoverable transaction journal and commit marker so a crash or I/O failure cannot make a partially published session eligible for MOC/index; the next run SHALL deterministically finish or roll back the prepared transaction before new work. Each atom's `slice_id` MUST be content-derived so identical content yields a stable ID across re-runs. The processing ledger record MUST carry `promoter="llm"` and the verifiable provenance defined by this change, including `skill_hash`.

#### Scenario: Session distilled into multiple atoms
- **WHEN** a session with multiple concepts is promoted with `promoter: llm`
- **THEN** the backend output is parsed into one or more knowledge atoms written under `knowledge/`, each with content-derived `slice_id`, canonical title, authoritative project, tags, per-batch relations, and distillation provenance, and one committed processing record identifies the complete session publication

#### Scenario: Validation failure aborts the whole session
- **WHEN** any produced atom fails a semantic or frontmatter validation gate
- **THEN** no atom for that session becomes publication-eligible, the session follows bounded retry/park handling, and failure evidence excludes raw backend output and session content

#### Scenario: Mid-publication failure is recovered atomically
- **WHEN** writing or relation materialization fails after some prepared atom files exist but before the session commit marker
- **THEN** those files remain ineligible for MOC/index and the next run uses the transaction journal to finish or roll back the complete session without duplicating atoms or edges

### Requirement: Fail-closed distillation under agent unavailability

LLM distillation MUST be fail-closed: when the configured backend command is missing, times out, exits non-zero, returns empty output, or returns unparseable/invalid JSON, no atom for that session SHALL be published and no source data SHALL be lost. A retryable failure SHALL remain in `split` only while its bounded retry budget remains; after the budget is exhausted it SHALL enter `parked` with sanitized failure evidence and SHALL NOT be retried automatically until an explicit gated requeue. Logs and evidence MUST NOT contain raw backend output, prompts, or session content.

#### Scenario: Backend unavailable follows bounded retry then park
- **WHEN** the service-effective backend is unavailable for a session attempt
- **THEN** the session remains unpublished, consumes at most the configured retry budget, then enters `parked` with a failure category and safe evidence until an explicit requeue

#### Scenario: Backend recovery permits explicit requeue
- **WHEN** deployment/config attestation and a service-effective backend probe pass after a session was parked
- **THEN** an operator MAY explicitly requeue a bounded batch without deleting the original raw session or prior failure evidence

## ADDED Requirements

### Requirement: Session semantic content preservation

The importer SHALL represent the generated session title separately from the adapter-provided assistant summary. Title generation MUST NOT overwrite or replace `assistant_summary`. The rendered inbox artifact SHALL use the generated session title for its title metadata and SHALL preserve the original assistant summary in the semantic body consumed by atomization. When old source data no longer contains the original summary, migration MUST report that evidence as unavailable and MUST NOT substitute a generated title as if it were the original summary.

#### Scenario: Generated title does not replace assistant outcome
- **WHEN** an adapter supplies an assistant summary and title generation produces a different session title
- **THEN** the inbox title metadata SHALL contain the generated title and the body summary SHALL retain the adapter-provided outcome byte-for-byte after normalization

#### Scenario: Irrecoverable historical summary is not invented
- **WHEN** a historical inbox artifact contains only the generated title and its source archive cannot reconstruct the original summary
- **THEN** migration SHALL leave the unavailable summary explicitly unresolved and MUST NOT copy the title into the summary field

### Requirement: Canonical semantic atom title before publication

Each LLM proposal title SHALL be persisted as canonical `title` and MAY also be mirrored to `atom_title` for compatibility. Before a session is recorded as promoted, every atom title MUST pass the same generic-title predicate used by retrieval-pool classification. The system MAY perform one bounded repair through the same canonical distiller; if the repaired title remains generic or invalid, the session SHALL follow the bounded retry/park path and no atom from that session SHALL be published. MOC naming MUST prefer canonical `title`, then compatibility `atom_title`, and MUST NOT replace a valid semantic title with an artifact/project fallback.

#### Scenario: Proposal title survives write, link, and index
- **WHEN** the LLM returns a specific valid title for an atom
- **THEN** that exact semantic title SHALL remain canonical through note write, MOC naming/linking, and retrieval indexing

#### Scenario: Generic output is not marked promoted
- **WHEN** the LLM returns a generic title and the bounded repair also fails the generic-title predicate
- **THEN** no atom from that session SHALL be published or recorded as promoted, and the failure SHALL enter bounded retry/park evidence

### Requirement: Rich project identity is distinct from filesystem placement

The atomizer SHALL treat a validated project-registry ID as authoritative metadata even when the ID is not a safe single path component. Filesystem placement SHALL derive a collision-resistant stable directory key from a readable normalized prefix plus a canonical-ID hash at the path boundary, without changing the canonical project ID stored in frontmatter and ledgers. A known source project SHALL be inherited by all atoms by default and MUST NOT be silently changed by the LLM. Only a source project that is explicitly `_unknown` MAY be resolved from the union of legacy and generated project registries.

#### Scenario: Remote-form project remains canonical
- **WHEN** an imported session carries a registry-valid remote-form project ID
- **THEN** the atom frontmatter and ledgers SHALL preserve that exact ID while the knowledge path uses its safe derived directory key

#### Scenario: LLM cannot re-home a known source project
- **WHEN** a session has a known source project but the LLM returns another project
- **THEN** the system SHALL retain the source project and record the proposal mismatch without publishing under the returned project

#### Scenario: Sanitizer collision does not merge projects
- **WHEN** two canonical project IDs would produce the same legacy sanitized path component
- **THEN** their hashed directory keys SHALL remain distinct and migration SHALL record any old-path-to-new-path mapping before moving knowledge

### Requirement: Verifiable and honest distillation provenance

Every promoted processing record and atom SHALL carry distillation provenance sufficient to identify the backend/provider, requested model, observed model when verifiable, model-verification status, command-or-endpoint fingerprint, config hash, skill hash, Hippo version, and build commit. Secrets, raw credentials, complete private endpoint URLs, and personal executable paths MUST NOT be persisted. A custom argv backend that does not return authenticated model identity MUST record the observed model as unknown/unverified; a configured label MUST NOT be represented as the observed model.

#### Scenario: Custom argv model cannot be proven
- **WHEN** a custom argv backend succeeds but returns no verifiable model identity
- **THEN** the atom SHALL record the requested model separately, observed model as null/unknown, and verification status as unverified

#### Scenario: Backend failure preserves safe diagnostics
- **WHEN** an agent process exits non-zero
- **THEN** retry/park evidence SHALL include a bounded sanitized stderr excerpt and exit code without storing the prompt, raw model output, credential-like values, or control sequences

### Requirement: Promotion and run-level publication integrity

Per-session promotion SHALL require valid semantic content, canonical non-generic title, authoritative project identity, valid frontmatter, and valid checksum before the processing ledger records `promoted`. The atomizer SHALL accept a dream `run_id`, return the exact `produced_slice_ids`, and persist their correlation so dream can reconcile them after MOC/index publication across disk metadata and both metadata/FTS retrieval surfaces. A missing or excluded atom SHALL make the run degraded or failed with an explicit reason; process exit success alone MUST NOT be interpreted as publication success.

#### Scenario: Written atom missing from index degrades run
- **WHEN** a session is promoted to a valid knowledge file but the run-level index reconciliation cannot find that atom
- **THEN** dream SHALL NOT report `ok` and SHALL expose the missing slice ID and reason in machine-readable health output

#### Scenario: Fully closed atomization run is ok
- **WHEN** all produced atoms pass semantic and integrity gates and appear in both metadata and FTS index surfaces
- **THEN** dream SHALL report `ok` with the reconciled produced/indexed counts
