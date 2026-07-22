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

LLM distillation MUST be fail-closed across the configured external-agent chain. When one profile is missing, ineligible, rate-limited, unavailable, times out, exits non-zero, returns empty output, or returns unparseable/invalid JSON, no atom for that attempt SHALL be published and no source data SHALL be lost. Only an explicitly allowlisted retryable category MAY advance to the next profile within the global fallback budget. The complete session SHALL restart from its frozen input with one profile pinned for that attempt; staged chunks from different profiles MUST NOT be combined. After the acyclic chain or global deadline/attempt/call budget is exhausted, the session SHALL enter `parked` once with sanitized attempt-chain evidence and SHALL NOT be retried automatically until an explicit gated requeue. Input-contract, policy, unsafe, invalid configuration, and context-budget failures MUST stop without fallback. Logs and evidence MUST NOT contain raw backend output, prompts, session content, credentials, credential env names, or secret paths.

#### Scenario: Backend unavailable follows bounded retry then park
- **WHEN** the service-effective backend is unavailable for a session attempt
- **THEN** the session remains unpublished, advances only through the bounded eligible profile chain, then enters `parked` once with per-attempt failure categories and safe evidence until an explicit requeue

#### Scenario: Backend recovery permits explicit requeue
- **WHEN** deployment/config attestation and a service-effective backend probe pass after a session was parked
- **THEN** an operator MAY explicitly requeue a bounded batch without deleting the original raw session or prior failure evidence

## ADDED Requirements

### Requirement: External CLI-only credential boundary

Hippo SHALL invoke distillation agents only as external headless CLI processes. Hippo MUST NOT accept, resolve, persist, log, or attest provider API-key values, credential env names, OAuth state, secret-file paths, provider base URLs, or direct provider HTTP/SDK configuration. Legacy `openai-compatible`, `base_url`, `api_key_env`, direct HTTP clients, provider proxy environment wiring, and direct Gemma TCP routes SHALL be removed from runtime and either rejected or explicitly removed by migration. Any prohibited direct-provider field containing a non-empty value—including a credential, credential env name, OAuth state, secret path, or provider URL—SHALL block migration as `operator-redaction-required`; Hippo SHALL report only the field/path and MUST NOT copy, back up, apply, resolve, or log the value. The operator MUST sanitize it outside Hippo before migration can continue. Atomization, importer title generation, and SkillOpt SHALL use the same external-agent router so no provider-specific side channel bypasses the profile and provenance contract.

#### Scenario: Legacy direct-provider configuration is rejected
- **WHEN** runtime or migration input contains a provider base URL, API-key field/env-name, or direct HTTP backend
- **THEN** normal processing SHALL fail before agent invocation and migration SHALL report the retired field without reading or copying any credential value

#### Scenario: External CLI owns authentication
- **WHEN** a selected CLI requires login or OAuth refresh
- **THEN** the CLI SHALL perform that lifecycle outside Hippo, and Hippo SHALL expose only a sanitized auth failure category eligible for policy-controlled fallback

### Requirement: Declarative external-agent profiles

Each profile SHALL declare at least `id`, `tier`, `priority`, typed `traits`, allowed `task_classes`, `model`, `effort`, `supported_efforts`, `argv`, `timeout`, eligibility constraints, and fallback policy. Dream SHALL select only profiles whose `task_classes` include `atomization` and whose zero-tool/no-MCP/no-custom-instructions/no-ask-user/no-remote restrictions are verifiably effective. `argv` SHALL be a token list executed with `shell=False`; aliases, shell functions, `bash -c`, `sh -c`, shell interpolation, permission bypass, `--yolo`, `--autopilot`, and tool-enabled execution are forbidden. The prompt SHALL be supplied only through stdin and `{PROMPT}` SHALL be invalid in command templates. Only complete-token `{MODEL}` and `{EFFORT}` placeholders MAY be rendered, after profile-specific allowlist validation. The executable SHALL resolve in the service environment; a `.bashrc` alias such as `cg` is insufficient and requires a real external launcher executable. Child processes SHALL receive a fixed minimal non-secret environment allowlist instead of inherited `os.environ`; provider credential variables and configurable credential pass-through are forbidden. A CLI that needs env-based authentication SHALL use an external launcher that obtains and injects credentials outside Hippo.

#### Scenario: Interactive shell alias is not deployable
- **WHEN** a profile command resolves only because an interactive shell defines an alias or function
- **THEN** service-effective validation SHALL mark the profile ineligible without invoking a shell

#### Scenario: Prompt in argv is rejected
- **WHEN** an agent template contains `{PROMPT}`, shell interpolation, or a prompt argument
- **THEN** configuration validation SHALL fail before any session content reaches a process argument or process list

#### Scenario: Unsupported effort is rejected
- **WHEN** configured effort is not in that profile's `supported_efforts`
- **THEN** configuration validation SHALL fail rather than passing a guessed flag or silently changing effort

#### Scenario: Child environment is minimized
- **WHEN** the systemd manager environment contains provider tokens or unrelated secrets
- **THEN** the selected agent SHALL receive only the fixed non-secret child-environment allowlist and no provider credential variable from Hippo

### Requirement: Deterministic tiered fallback

The default ordered groups SHALL be Tier 1 `claude` and `codex` as difficult-decision judges, Tier 2 `agy` and `cg` as fast-response/heavy-work agents, and Tier 3 `co-gem`, `claude-gem`, and custom local profiles as low-cost fallback. Exact order within a tier SHALL use explicit numeric priority. Traits SHALL be reviewable routing metadata, not free-form instructions that permit the model to choose its successor. The fallback graph SHALL be acyclic and constrained by a global deadline, maximum attempts, maximum agent calls, and per-profile circuit breaker/cooldown. Dream profiles SHALL disable CLI-native model fallback/retry; a CLI for which preflight cannot prove native fallback is disabled SHALL be ineligible, so Hippo remains the sole routing and budget authority.

A valid `no_findings` response SHALL be success and MUST NOT trigger fallback. Allowlisted profile-ineligible, auth, rate-limit, capacity, timeout, transport/process, empty-output, and invalid-output categories MAY advance; deterministic input-contract, policy/config, unsafe, or context-budget failures MUST NOT. Success after one or more failed profiles SHALL be reported as `degraded-success`, retaining every prior failure and the fallback reason. Exhaustion SHALL park the session once.

#### Scenario: Primary auth failure falls back deterministically
- **WHEN** the first Tier 1 profile returns a sanitized auth failure and policy allows fallback
- **THEN** the next eligible profile in explicit priority order SHALL restart the complete session from frozen input and successful output SHALL be marked `degraded-success`

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

#### Scenario: Agent configuration change invalidates cache
- **WHEN** task class, response schema, router contract, profile, model, effort, command template, config, skill, or prompt changes
- **THEN** the previous cache entry SHALL not be reused and provenance SHALL identify the new request independently

### Requirement: Session semantic content preservation

The importer SHALL represent the generated session title separately from all adapter-provided assistant outcomes. A normalized session SHALL preserve every ordered, complete assistant output in `assistant_messages`; `assistant_summary` SHALL remain a compatibility field equal to the final non-empty assistant message. Neither field MAY be truncated by the importer. Title generation MUST NOT overwrite or replace either semantic field. The rendered inbox artifact SHALL use the generated `session_title` for title metadata and SHALL preserve the original assistant outcomes in the semantic body consumed by atomization. Each snapshot SHALL carry a unique `capture_id`; an explicit `parent_session_id` SHALL be retained only when supported by source evidence. Legacy payloads SHALL derive `capture_id` from the byte-preserved raw payload SHA-256. When old source data no longer contains the original outcome, migration MUST report that evidence as unavailable and MUST NOT substitute a generated title as if it were the original outcome.

#### Scenario: Generated title does not replace assistant outcome
- **WHEN** an adapter supplies an assistant summary and title generation produces a different session title
- **THEN** the inbox title metadata SHALL contain the generated title and the body summary SHALL retain the adapter-provided outcome byte-for-byte after normalization

#### Scenario: Irrecoverable historical summary is not invented
- **WHEN** a historical inbox artifact contains only the generated title and its source archive cannot reconstruct the original summary
- **THEN** migration SHALL leave the unavailable summary explicitly unresolved and MUST NOT copy the title into the summary field

#### Scenario: Multiple full assistant outcomes survive normalization
- **WHEN** a transcript contains multiple assistant text messages including one longer than 2,000 characters
- **THEN** `assistant_messages` SHALL preserve all messages in source order without truncation and `assistant_summary` SHALL equal the final non-empty message

#### Scenario: Capture identity does not collapse changed content
- **WHEN** two snapshots share tool and session ID but have different capture IDs or different ordered semantic content
- **THEN** both raw snapshots SHALL be archived and the newer semantic content SHALL not be discarded by a coarse completeness comparison; when both snapshots carry comparable capture timestamps, an older late-arriving snapshot SHALL NOT replace the newer canonical inbox artifact

### Requirement: Bounded zero-tool distillation with a 32K minimum provider context

Every eligible Dream external-agent profile SHALL declare a provider context of at least 32,768 tokens, while retaining fixed limits of at most 12,000 deterministically estimated input tokens, 2,048 output tokens, a 10 percent input safety margin, and an independent 48 KiB UTF-8 prompt-transport gate. A larger provider context MUST NOT raise these fixed execution limits. Fixed skill, schema, and project-registry prompt content SHALL be charged before session fragments. Fragments SHALL be packed in source order; an oversized individual fragment SHALL be split deterministically at paragraph boundaries, labeled `part n/m`, and fully covered without tail truncation. Chunks SHALL execute sequentially with parallelism one, a 300-second timeout, and at most two attempts per chunk. All chunk outputs SHALL remain staged until every chunk succeeds; the system SHALL use only deterministic local deduplication and MUST NOT invoke a reducer model. A budget that cannot be satisfied SHALL fail closed as `context_budget_exceeded`.

Each profile SHALL use its CLI-specific flags or isolation mechanism to enforce the equivalent of no available tools, no builtin MCPs, no custom instructions, no user interaction, and no remote export; inability to prove the restriction SHALL make that profile ineligible rather than silently falling back to a tool-enabled mode.

#### Scenario: Large session is fully covered by ordered chunks
- **WHEN** a session is approximately 53K estimated tokens
- **THEN** the atomizer SHALL produce multiple ordered stdin prompts whose fragment-part coverage is complete and non-overlapping, each within both token and prompt-transport budgets

#### Scenario: Provider boundary is explicit
- **WHEN** operator-declared provider context is 32,767, 32,768, 32,769, or 262,144 tokens
- **THEN** only values at least 32,768 satisfy the provider gate and all accepted prompts still satisfy the stricter 12,000-token and 48 KiB gates

### Requirement: Explicit canonical LLM disposition

The canonical response SHALL be exactly an object with fields `schema_version`, `disposition`, `reason`, and `findings`. `schema_version` SHALL equal `1`; `disposition` SHALL be either `findings` or `no_findings`; unknown fields and surrounding non-whitespace noise SHALL be invalid. `findings` SHALL contain one or more valid proposals and use `reason=null`; one malformed proposal SHALL invalidate the entire response rather than publish a salvageable subset. When the source session has a known project, that pinned source project SHALL override model re-homing. `no_findings` SHALL contain an empty findings list and a non-empty reason. During one compatibility version, a non-empty legacy proposal array MAY be accepted. A legacy empty array, empty wrapper, empty stdout, malformed type, or unknown field SHALL be invalid and MUST NOT produce `promoted`.

`promoted` SHALL require `accepted_slices >= 1`. Only explicit successful `no_findings` responses from every chunk MAY terminate with zero slices, using the distinct terminal state `no-findings` and retaining the reasons.

Parked evidence SHALL retain only structured failure metadata plus the byte count and SHA-256 of invalid model stdout; it MUST NOT persist the stdout text because a backend can echo private prompt content. Each chunk attempt SHALL clear any previous chunk's in-memory stdout before execution.

#### Scenario: Empty legacy array is invalid
- **WHEN** the backend returns `[]` or a wrapper containing an empty findings array without an explicit `no_findings` disposition and reason
- **THEN** the attempt SHALL consume the bounded invalid-output retry path and MUST NOT record `promoted`

#### Scenario: Explicit no-findings terminates without a slice
- **WHEN** every chunk returns a valid `no_findings` response with a non-empty reason
- **THEN** the session SHALL enter terminal `no-findings`, archive its fragments, and never create a zero-slice promoted record

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

Every promoted processing record and atom SHALL carry distillation provenance sufficient to identify the profile revision, tier, attempt index, requested model and effort, observed model when verifiable, model-verification status, command fingerprint, fallback reason, config hash, skill hash, Hippo version, and build commit. Secrets, credential env names, secret paths, raw credentials, provider URLs, and personal executable paths MUST NOT be persisted. An external CLI that does not return authenticated model identity MUST record the observed model as unknown/unverified; a configured label MUST NOT be represented as the observed model.

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
