# stage2-memory-governance Specification

## Purpose
Define the Stage 2 memory governance contract, including the repo-local importer
and hook substrate that writes canonical session artifacts into
`~/.agents/memory/`.
## Requirements
### Requirement: Canonical memory routing path

Stage 2 SHALL fix the canonical memory path as `inbox → work-centric → knowledge`. No producer MAY write directly to `knowledge` without first passing through `inbox` and `work-centric`. `inbox` MUST retain original source and ingestion metadata but MUST NOT serve as the long-term query entrypoint. `work-centric` MUST aggregate by project / workstream / story and MUST be where the classifier performs dedup, correlation, and replay-candidate preparation. `knowledge` MUST only hold reusable, citable, replayable conclusions that already have provenance and source references.

#### Scenario: Scope declares the full path

- **WHEN** a reviewer reads `openspec/specs/stage2/scope.md`
- **THEN** the document MUST contain the phrase `inbox -> work-centric -> knowledge` in §2 (memory routing rules)

#### Scenario: Each layer states its own constraint

- **WHEN** a reviewer reads `openspec/specs/stage2/scope.md` §2
- **THEN** the document MUST describe all three layers with their respective constraints (inbox retains metadata but is not query entry, work-centric is where classifier runs, knowledge requires provenance)

### Requirement: Importer / classifier / replay / janitor boundary

Stage 2 SHALL separate `importer`, `classifier`, `replay`, and `janitor` as independently verifiable components. `janitor` MUST be declared as an independent service — not a pipeline tail step. `replay bundle` MUST read only distilled artefacts and ledger events; it MUST NOT scan raw prompts. `importer / classifier / replay` MUST each be independently testable so they can serve as sync-back gate prerequisites.

#### Scenario: Janitor is an independent service

- **WHEN** a reviewer reads `paulshaclaw/janitor/service.md`
- **THEN** the document MUST state janitor is an independent service (not attached to the ingestion pipeline) and MUST describe its guardrails

#### Scenario: Replay reads only distilled artefacts

- **WHEN** a reviewer reads `openspec/specs/stage2/scope.md` §4 or `paulshaclaw/memory/routing.md`
- **THEN** replay MUST be declared to read only distilled artefacts and ledger events, never raw prompts

### Requirement: decayed and reactivation events

Stage 2 SHALL treat `decayed` and `reactivation` as first-class ledger events, not implicit states. A `decayed` event MUST fire when a fact expires, its source becomes invalid, or it is superseded by conflicting evidence; the handler MUST retain the original reference, annotate the decay reason, and remove the record from the high-trust retrieval set. A `reactivation` event MUST fire when new evidence, human confirmation, or replay validation re-supports an existing record; the handler MUST append a `record-agent-reference` and restore retrieval weight.

#### Scenario: Scope names both events

- **WHEN** a reviewer reads `openspec/specs/stage2/scope.md` §3
- **THEN** the document MUST contain the literal string `decayed/reactivation` and describe the trigger + action for each event

### Requirement: Executable sync-back gate to custom-skills

Stage 2 SHALL provide an executable sync-back gate in `paulshaclaw/memory/syncback/` that governs when the project-tuned `paulsha-memory` package may be synced back to `hamanpaul/custom-skills`. The gate MUST evaluate all of: (a) importer / classifier / replay tests pass, (b) decayed/reactivation tests pass and evidence is present, (c) evidence files under `docs/superpowers/workstreams/stage2-paulsha-memory/evidence/` exist and are non-empty, (d) `review.md` contains a mergeable conclusion with no live blocking marker, (e) `lifecycle.schema.REQUIRED_FRONTMATTER_FIELDS` exactly matches the Stage 3 canonical required set `{phase, project, slice_id, artifact_kind, version, created_at, created_by, source_session, gate_required, supersedes, checksum}`. The verdict MUST report each condition's pass/fail with non-sensitive detail and an overall `ok` that is true only when all conditions pass. The gate MUST be exposed as `psc memory syncback check`, return exit 0 on pass and non-zero otherwise, and remain read-only: it MUST NOT copy the package into `custom-skills/paulsha-memory/` nor push to `hamanpaul/custom-skills`.

#### Scenario: All conditions pass yields exit zero and a sync manifest

- **WHEN** all five conditions hold and an operator runs `psc memory syncback check`
- **THEN** the verdict `ok` MUST be true and the command MUST exit zero
- **THEN** a sync manifest listing the installable package paths MUST be reported for manual follow-up only

#### Scenario: Any failing condition blocks the gate

- **WHEN** any one of the five conditions fails
- **THEN** the verdict `ok` MUST be false, the command MUST exit non-zero, and the sync manifest MUST be empty

### Requirement: Sync-back gate is fail-closed and deterministic

The sync-back gate MUST be fail-closed: a missing or unreadable file, an invalid UTF-8 review artifact, a test runner that raises, an all-skipped required suite, a disabled test run, or a `review.md` lacking a canonical `結論` / `Conclusion` section MUST cause the corresponding condition to fail and therefore the gate to fail — never a default pass. The gate MUST be deterministic: the timestamp MUST be injected rather than read from the wall clock inside the evaluator, and the test runner MUST be injectable so the gate's own unit tests do not invoke the real suite. Condition details MUST NOT contain secrets or raw exception text.

#### Scenario: Test runner failure fails closed

- **WHEN** the injected test runner raises or reports failure
- **THEN** the corresponding test condition MUST fail and the gate MUST fail

#### Scenario: Skipping tests is not a pass

- **WHEN** the gate is run with test execution disabled, or a required suite reports only skipped tests
- **THEN** the affected test condition MUST be reported as failed

#### Scenario: Missing review conclusion fails closed

- **WHEN** `review.md` has no canonical `結論` / `Conclusion` section
- **THEN** the review condition MUST fail

### Requirement: Stage 2 integration check

Stage 2 SHALL ship `paulshaclaw/memory/tests/stage2_integration_check.sh` as a guard that fails fast under `set -euo pipefail` and validates seven surfaces via literal `grep -Fq` against specific phrases: scope routing phrase, memory routing inbox/knowledge, janitor systemd/reactivation, sync-back gate phrases, Stage 3 frontmatter field names (slice_id / artifact_kind / supersedes / checksum), evidence template sections, and review.md non-blocking conclusion. Running the script in a clean checkout MUST exit zero and print `[stage2] ok`.

#### Scenario: Clean run exits zero

- **WHEN** an operator runs `bash paulshaclaw/memory/tests/stage2_integration_check.sh` from the repo root on main
- **THEN** the script MUST exit with code 0 and its last line MUST be `[stage2] ok`

#### Scenario: Missing frontmatter field is caught

- **WHEN** any of `slice_id`, `artifact_kind`, `supersedes`, or `checksum` is removed from `openspec/specs/stage2/scope.md`
- **THEN** the script MUST exit non-zero at the "validate Stage 3 frontmatter field names named explicitly" check

### Requirement: Stage 2 evidence and archive layout

Stage 2 SHALL keep workstream artefacts under `docs/superpowers/workstreams/stage2-paulsha-memory/`. The directory MUST contain `plan.md`, `task.md`, `todo.md`, `review.md`, and an `evidence/` subdirectory with at least `README.md` and `stage2-integration-template.md` describing the test command and evidence file-naming convention. The repo SHALL also keep a handoff note at `docs/superpowers/archive/stage2-paulsha-memory-*.md` summarising the landing.

#### Scenario: Workstream directory contains required artefacts

- **WHEN** a reviewer lists `docs/superpowers/workstreams/stage2-paulsha-memory/`
- **THEN** the directory MUST contain `plan.md`, `task.md`, `todo.md`, `review.md`, and a non-empty `evidence/` subdirectory with at least `README.md` and `stage2-integration-template.md`

### Requirement: Stage 2 memory policy boundary contract

Stage 2 SHALL define a memory security policy boundary contract with the canonical boundary identifiers `external_to_raw`, `raw_to_distilled`, `distilled_to_canonical`, `canonical_to_indexed`, and `indexed_to_consumer`. The boundary identifiers SHALL reuse the existing Stage 2 memory layer model and SHALL NOT redefine the physical memory tree. MVP execution MUST enforce policy at `external_to_raw` and `raw_to_distilled`; the other boundaries MUST be reserved in policy schema for future Stage 2 components.

#### Scenario: Boundary schema lists all five identifiers

- **WHEN** an operator reads `paulshaclaw/memory/policy/boundaries.yaml`
- **THEN** it MUST list `external_to_raw`, `raw_to_distilled`, `distilled_to_canonical`, `canonical_to_indexed`, and `indexed_to_consumer`
- **THEN** `external_to_raw` and `raw_to_distilled` MUST be marked mandatory for MVP execution
- **THEN** the remaining three boundaries MUST be marked deferred

### Requirement: Policy artifacts and effective policy hash

Stage 2 SHALL store default policy artifacts in `paulshaclaw/memory/policy/secrets.yaml`, `classification.yaml`, and `boundaries.yaml`. Stage 2 SHALL support a local override file at `~/.config/paulshaclaw/policy.override.yaml`. The policy loader MUST merge defaults and local override into an effective policy and compute a deterministic `effective_policy_hash`. Ledger and audit records written by the policy layer MUST include both `policy_version` and `effective_policy_hash`.

#### Scenario: Local override participates in effective hash

- **WHEN** a local override disables a rule for a session
- **THEN** the effective policy hash MUST change
- **THEN** dry-run, audit, and ledger output for that session MUST include the changed hash

#### Scenario: Unsupported major version fails closed

- **WHEN** a consumer loads a policy with an unsupported major `policy_version`
- **THEN** fail-closed boundaries MUST reject processing
- **THEN** no inbox artifact MAY be published

### Requirement: Redaction at ingress boundaries

Stage 2 SHALL run cheap regex redaction at `external_to_raw` before hook scripts write queue payloads. Stage 2 SHALL run the full policy library and gitleaks detector at `raw_to_distilled` before importer writes `inbox/`. Redaction action MUST be line-level: every line with one or more hits MUST be replaced with `[REDACTED LINE: <rule-id> x<count>]` or an equivalent multi-rule placeholder. The memory system MUST NOT store matched secret text or original matched lines in `inbox/`, ledger, audit, `archive/queue/`, or `runtime/queue/_failed/`.

#### Scenario: Hook redacts before queue write

- **WHEN** a hook payload contains an obvious GitHub PAT fixture
- **THEN** the queue payload MUST contain a redacted line placeholder
- **THEN** the queue payload MUST NOT contain the original token

#### Scenario: Gitleaks-only hit is not archived raw

- **WHEN** gitleaks finds a secret in a queue payload that hook regex missed
- **THEN** the importer MUST write only redacted content to `inbox/`
- **THEN** any `archive/queue/` copy MUST be redacted
- **THEN** the original queue payload MUST be unlinked after successful processing

### Requirement: Classification tagging for distilled artifacts

Stage 2 SHALL classify every distilled `inbox/` artifact with `classification_level`, `classification_reason`, `classification_policy_hash`, and `classification_source`. Allowed levels MUST be `public`, `private`, and `secret`. Unknown projects MUST default to `private`. Any artifact with redaction hits MUST be classified as `private` unless a more restrictive rule applies. Classification failure MUST fail open with warning by writing the artifact as `private` and recording `classification-warning`.

#### Scenario: Unknown project defaults private

- **WHEN** the project resolver returns `_unknown`
- **THEN** the inbox artifact MUST contain `classification_level: private`
- **THEN** `classification_reason` MUST indicate the unknown-project fallback

#### Scenario: Redaction hit downgrades classification

- **WHEN** a session would otherwise be `public` but has any redaction hit
- **THEN** the final inbox artifact MUST be classified `private`

### Requirement: Policy audit and ledger records

Stage 2 SHALL record session-level policy summaries in the importer ledger and rule-level events in `~/.agents/memory/runtime/audit/policy.jsonl`. Both record types MUST include `session_ref`, `policy_version`, and `effective_policy_hash`. Audit records MUST include boundary, component, rule ID, detector, line number when available, and action. Audit and ledger records MUST NOT contain matched secret text or original line content.

#### Scenario: Audit contains rule metadata but no secret

- **WHEN** a redaction rule matches a token fixture
- **THEN** `policy.jsonl` MUST contain the rule ID, detector, boundary, action, and line number
- **THEN** `policy.jsonl` MUST NOT contain the matched token or original line text

### Requirement: Policy failure behavior

Stage 2 SHALL treat redaction failures at `external_to_raw` and `raw_to_distilled` as fail-closed with retry. Retry exhaustion MUST write only a metadata failure stub under `runtime/queue/_failed/`, unlink the queue payload, avoid publishing inbox output, and write `policy-error` to the ledger when the ledger is available. If the ledger is unavailable, the failure stub MUST say `ledger_status: unavailable` and the component MUST write a best-effort warning to `~/.agents/memory/log/policy.log`. Classification failures MUST fail open with `private` fallback and warning.

#### Scenario: Gitleaks missing fails closed

- **WHEN** gitleaks is enabled but unavailable
- **THEN** `raw_to_distilled` MUST retry according to `boundaries.yaml`
- **THEN** retry exhaustion MUST NOT write an inbox artifact
- **THEN** `_failed/` MUST contain only metadata stub fields, not the original payload

#### Scenario: Ledger unavailable does not claim policy-error was recorded

- **WHEN** a fail-closed policy error occurs and ledger append fails after retry
- **THEN** the failure stub MUST include `ledger_status: unavailable`
- **THEN** no requirement MAY claim a ledger `policy-error` entry exists for that event

### Requirement: Local override and dry-run policy workflow

Stage 2 SHALL support audited local override through `~/.config/paulshaclaw/policy.override.yaml`. Overrides MAY disable rules globally, disable rules for specific sessions, append local regex rules, append local classification rules, and override project defaults. Stage 2 SHALL provide `psc memory dry-run-policy <session-id>` that reports rule IDs, detector, line number, action, classification result, and effective policy hash without writing inbox output or printing matched strings.

#### Scenario: Rule disabled for one session

- **WHEN** local override disables `rule-x` for `session-a`
- **THEN** dry-run for `session-a` MUST report `rule-x` as skipped
- **THEN** dry-run for another session MUST still apply `rule-x`
- **THEN** audit output MUST include an override event without secret text

### Requirement: Consumer policy API enforcement

Stage 2 SHALL provide `paulshaclaw.memory.policy` as the only supported policy execution API. Memory consumers MUST NOT parse policy YAML directly, implement their own detector, or write policy audit records by hand. The repository SHALL include CI lint that fails when a memory consumer writes or emits memory across a declared boundary without calling the policy boundary API.

#### Scenario: Consumer bypass is caught by lint

- **WHEN** a Python memory consumer fixture writes to a memory boundary without calling `paulshaclaw.memory.policy`
- **THEN** `paulshaclaw/memory/lint/policy_consumer_lint.py` MUST exit non-zero

### Requirement: Canonical agent memory tree

Stage 2 SHALL provision `~/.agents/memory/` as the canonical agent memory substrate, fully disjoint from any Obsidian vault (`~/notes/`). The tree MUST contain `inbox/{sessions,plans,research,reports}/<tool>/<YYYY-MM-DD>/`, `work-centric/`, `knowledge/`, `runtime/{queue,queue/_failed,locks,ledger,indexes}/`, `log/`, `hooks/`, and `archive/queue/<YYYY-MM>/`. Directory mode MUST be 0700. `work-centric/`, `knowledge/`, and `runtime/indexes/` MUST be created as empty placeholders in this MVP; only `inbox/`, `runtime/queue*`, `runtime/locks`, `runtime/ledger`, `log/`, `hooks/`, and `archive/queue/` are active write targets in the repo-local implementation.

#### Scenario: Install creates the canonical tree

- **WHEN** an operator runs `~/.agents/memory/hooks/install.sh --tree-only`
- **THEN** all directories listed above MUST exist with mode 0700
- **THEN** placeholder subtrees MUST contain a `.gitkeep` file but no other content
- **THEN** `~/notes/` MUST NOT be touched

### Requirement: Hook-based session ingestion for three CLIs

Stage 2 SHALL provide native hook integrations for Claude Code (`SessionEnd`), Codex CLI (`Stop` and `SubagentStop`), and GitHub Copilot CLI (`sessionEnd`). Hook scripts MUST be thin: they MUST write the raw payload into `~/.agents/memory/runtime/queue/` via atomic rename and fire-and-forget invoke `paulshaclaw.memory.importer.cli ingest --queue-item <path>`. Claude Code and GitHub Copilot CLI MAY use stable `<tool>__<session-id>.json` queue paths; Codex CLI MUST use a distinct per-event queue filename under `codex__<session-id>__<event-id>.json` because `Stop` and `SubagentStop` can fire multiple times within one session. Hook scripts MUST tag every payload with a `capture_scope` of `session_end`, `turn`, or `subagent`. Hook scripts MUST NOT raise to the host CLI; any failure MUST be logged to `~/.agents/memory/log/hooks.log` and the script MUST exit zero.

#### Scenario: Claude SessionEnd writes a queue payload

- **WHEN** an authorized operator finishes a Claude Code session
- **THEN** `~/.agents/memory/runtime/queue/claude-code__<sid>.json` MUST appear within the hook timeout
- **THEN** the payload MUST include `capture_scope: "session_end"`
- **THEN** `~/.agents/memory/log/hooks.log` MUST NOT contain an ERROR entry for that session

#### Scenario: Codex Stop is treated as turn snapshot

- **WHEN** Codex CLI fires `Stop` mid-session
- **THEN** the hook MUST write a distinct queue payload for that event with `capture_scope: "turn"` and `ended_at: null`
- **THEN** the hook MUST NOT treat the event as session termination

#### Scenario: Copilot sessionEnd renames camelCase keys

- **WHEN** Copilot CLI fires `sessionEnd` with payload keys `sessionId`, `timestamp`, `cwd`, `reason`
- **THEN** the hook MUST normalize `sessionId` to `session_id`
- **THEN** the hook MUST supplement missing transcript fields by reading `~/.copilot/history-session-state/session_<sid>_*.json`

### Requirement: Session-start wake-up injection

Stage 2 SHALL provide a `paulshaclaw/memory/wakeup/` code module that builds a per-project wake-up brief and injects it at session start. The brief MUST consist of the project's MOC primer (the T7 `knowledge/<project>-moc.md` body) followed by the most recent K active knowledge slices rendered as compact pointers (title, a one-line summary, and an Obsidian wikilink). Recency MUST be derived from the lifecycle ledger's `last_event_ts` and filtered to active records via the retrieval set; the builder MUST be deterministic given its inputs (no wall-clock inside the builder) and MUST be read-only (no knowledge or ledger writes). The brief MUST be bounded by a character budget, reserving the recent-K block before the MOC and truncating only the MOC tail. Injection MUST use each CLI's additional-context channel: Claude `SessionStart`, Copilot `sessionStart`; Codex reuses Claude's hook. Injection MUST be fail-open: any error yields an empty brief, is logged, and exits zero without blocking session start.

#### Scenario: Brief surfaces project map and recent activity

- **WHEN** `psc memory wakeup --project <p>` runs and project `<p>` has a MOC and active slices
- **THEN** the output MUST contain the MOC primer and the most recent active slices, newest first
- **THEN** each recent entry MUST include a one-line summary and an Obsidian wikilink

#### Scenario: Unknown or empty project injects nothing

- **WHEN** the resolved project is `_unknown`, or the project has no MOC and no active slices
- **THEN** the brief MUST be empty and the hook MUST exit zero without injecting

#### Scenario: Session-start hook fails open

- **WHEN** the wake-up builder raises or input is malformed at session start
- **THEN** the hook MUST emit a valid empty-context payload, log the error, and exit zero
- **THEN** session start MUST NOT be blocked

#### Scenario: Brief respects the character budget

- **WHEN** the MOC body exceeds the remaining budget
- **THEN** the recent-K block MUST be preserved and only the MOC tail truncated with a truncation marker
- **THEN** the total brief length MUST NOT exceed the configured character budget

### Requirement: Pre-compaction session capture

Stage 2 SHALL provide PreCompact hooks (Claude `PreCompact`, Copilot `preCompact`) that snapshot the current session into the existing importer before compaction, tagging the snapshot with `capture_scope="pre_compact"`. The importer's scope ranking MUST treat `pre_compact` as a turn-level snapshot (rank 0) so that a later `session_end` or `watcher_final` capture supersedes it through the existing idempotency engine, without extending any schema. Pre-compaction capture MUST NOT perform atomization (deferred to dream) and MUST be fail-open: any error is logged and exits zero without blocking compaction.

#### Scenario: PreCompact writes a pre_compact snapshot

- **WHEN** compaction is triggered and a PreCompact hook fires with a session payload
- **THEN** a queue payload tagged `capture_scope: "pre_compact"` MUST be written and the importer triggered

#### Scenario: pre_compact is superseded by a more complete capture

- **WHEN** a `session_end` or `watcher_final` capture for the same session arrives after a `pre_compact` snapshot
- **THEN** the existing idempotency engine MUST treat the later capture as more complete (higher scope rank)

#### Scenario: PreCompact never blocks compaction

- **WHEN** the PreCompact hook errors or receives malformed input
- **THEN** it MUST log the error and exit zero
- **THEN** compaction MUST proceed unblocked

### Requirement: Content-hash and completeness idempotency

Stage 2 SHALL deduplicate session imports using `idempotency_key = "<source_agent>:<source_session>"`. The importer MUST compute `content_hash = sha256(canonical_json((session_id, capture_scope, turn_count, ended_at, sorted(touched_files), len(user_prompts))))` and `completeness = (scope_rank, turn_count, len(touched_files), len(user_prompts))` where `scope_rank` maps `{turn:0, subagent:0, pre_compact:0, session_end:1, watcher_final:2}`. For each incoming payload, after acquiring a `flock` on `runtime/locks/<key>.lock`, the importer MUST compare against the last ledger record for that key and resolve to exactly one of these statuses: `written`, `hash-duplicate`, `updated`, or `stale-skip`. Every decision MUST append a record to `~/.agents/memory/runtime/ledger/import.jsonl`.

#### Scenario: Higher completeness yields updated

- **WHEN** the importer processes a payload whose `completeness` is strictly greater (per Python tuple ordering) than the recorded entry
- **THEN** the importer MUST overwrite the inbox file
- **THEN** the ledger MUST receive an entry with `status: "updated"` including `from_completeness` and `to_completeness`

### Requirement: Project identity resolution with longest-prefix

Stage 2 SHALL resolve each session payload to a project identity using `~/.agents/config/projects.yaml`. Resolution MUST attempt, in order: (1) longest-prefix match of payload `cwd` against any project's `roots`; (2) longest-prefix match of explicit git toplevel against `roots`; (3) normalized match of remote URLs against `remotes`. If no rule hits, the importer MUST set `project: _unknown` and continue without raising. On alias collision, the first definition in `projects.yaml` wins and a WARN entry MUST be emitted through the importer logger.

#### Scenario: Monorepo child wins over parent

- **WHEN** `projects.yaml` declares both `monorepo` with root `/repo` and `monorepo-web` with root `/repo/web`, and the payload `cwd` is `/repo/web/src`
- **THEN** the resolver MUST return `monorepo-web`

### Requirement: Frontmatter contract for inbox entries

Stage 2 SHALL produce every inbox markdown file with a YAML frontmatter block whose required fields match `docs/research/02.obs-auto-moc-memory-dream-mode-24-7-service-notes-.md` lines 220–234, aligned with Stage 3 frontmatter. The MVP MUST NOT introduce new frontmatter fields beyond that contract. Missing best-effort fields MUST appear as deterministic fallback scalars (for example `_unknown`) or empty collections, never as `null` or as missing keys.

#### Scenario: Lint passes on repo-local fixtures

- **WHEN** an operator runs `paulshaclaw/memory/lint/frontmatter_lint.py` over importer output produced from the repo-local fixture set
- **THEN** the lint MUST report zero failures

### Requirement: Atomizer post-import promotion pipeline

Stage 2 SHALL provide a deterministic `atomizer` component that promotes records along `inbox → knowledge` as an independent post-import step. The atomizer MUST NOT modify the Stage 2 importer (`paulshaclaw.memory.importer`); it MUST only read importer output from the raw layer and consume it. Promotion MUST run as two independently re-entrant passes: a `split_pass` (raw session → deterministic fragments) and a `promote_pass` (fragments → knowledge slices). Each pass MUST derive its work-list from the filesystem plus the processing ledger so a crash between passes resumes safely on the next run.

#### Scenario: Importer is untouched

- **WHEN** a reviewer inspects the atomizer change
- **THEN** no file under `paulshaclaw/memory/importer/` MAY be modified
- **THEN** the atomizer MUST consume raw session documents written by the importer without altering importer code or output paths

#### Scenario: Passes are re-entrant

- **WHEN** `split_pass` has written fragments and recorded `state=split` but `promote_pass` has not run
- **THEN** a subsequent run MUST complete `promote_pass` for that session without re-splitting

### Requirement: Deterministic structural splitter

Stage 2 SHALL split each raw session document into fragments using deterministic structural boundaries (turn, heading, artifact markers) configured in `atomizer.yaml`. The splitter MUST be a pure function of `(session body, config)` with no LLM call, no randomness, and no wall-clock dependence. Fragment order and `fragment_index` MUST be deterministic. An empty or whitespace-only session MUST yield zero fragments without error.

#### Scenario: Same input yields same fragments

- **WHEN** the splitter runs twice over identical input and config
- **THEN** it MUST return identical fragments in identical order

#### Scenario: Oversize fragment is bounded

- **WHEN** a structural fragment exceeds `split.max_fragment_chars`
- **THEN** the splitter MUST further split it deterministically rather than emit an unbounded fragment

### Requirement: Per-session LLM semantic promoter

Stage 2 SHALL define the `Promoter` interface at session scope: `promote(fragments, config) -> list[Slice]`. Stage 2 SHALL provide an `LLMPromoter` that may merge multiple fragments into one slice or split one fragment into several, while preserving the deterministic `IdentityPromoter` under the same per-session signature. The splitter, ledgers, frontmatter builder, and flow-through MUST remain reusable across both promoters.

#### Scenario: IdentityPromoter remains 1:1 under per-session signature

- **WHEN** `IdentityPromoter.promote(fragments, config)` is called
- **THEN** it MUST return exactly one slice per input fragment

#### Scenario: LLM promoter may merge fragments

- **WHEN** the LLM promoter promotes a session whose fragments describe one concept across two fragments
- **THEN** it MAY return a single slice whose `source_fragment_indices` lists both fragments

### Requirement: Configurable agent-exec backend

Stage 2 SHALL drive the LLM promoter through a configurable `agent_exec` backend that invokes a one-shot subprocess command (default `scripts/claude-gemma4`, a local model). The backend MUST NOT require an API key or per-token billing; authentication, if any, is the invoked agent's responsibility. The agent command MUST be a configuration value and MUST be shareable with the paulshiabro `/agent` launcher configuration. The promoter MUST accept an injectable client so tests can substitute a fake without a real model.

#### Scenario: Agent command is configurable

- **WHEN** an operator sets `agent_exec.command` in `atomizer.yaml` or its override
- **THEN** the promoter MUST invoke that command rather than a hardcoded one

#### Scenario: Tests run without a real model

- **WHEN** the promoter is constructed with a fake agent client
- **THEN** promotion MUST complete deterministically using the fake's canned output, with no network or model dependency

### Requirement: Seed atomization skill document

Stage 2 SHALL ship a seed atomization skill document at `paulshaclaw/memory/atomizer/skills/atomize-knowledge-slice.md` that instructs the agent how to split, merge, tag, and relate session content into knowledge slices, and that declares the mandatory JSON output contract. Atomization behavior MUST live in this skill document, not be hardcoded in prompt assembly.

#### Scenario: Skill declares the output contract

- **WHEN** a reviewer reads the seed skill document
- **THEN** it MUST describe the required JSON output schema the agent must emit, including `artifact_kind`, `project`, `tags`, `body`, `source_fragment_indices`, and `relations`

### Requirement: Atomizer prompt forbids execution and prose

Stage 2 SHALL require the atomize-knowledge-slice prompt, including the final rendered runtime prompt, to instruct the model to return only an inline JSON array, and to NOT perform file create/write actions and NOT return prose, narration, summaries, markdown fences, or any text before/after the JSON array.

#### Scenario: Prompt states the inline-array-only contract

- **WHEN** the atomizer prompt is rendered for a promotion attempt
- **THEN** it MUST explicitly require an inline JSON array response and forbid file-creating/writing actions and prose narration

### Requirement: Knowledge slice frontmatter union contract

Stage 2 SHALL produce knowledge slices whose frontmatter is the union of the Topic 4 janitor read contract and the Stage 3 frontmatter schema, plus a Stage-2-owned `tags` field. Each slice frontmatter MUST pass `paulshaclaw.lifecycle.schema.validate_frontmatter` and MUST also expose the Topic 4 fields (`memory_layer=knowledge`, `source_agent`, `captured_at`, `provenance`, `supersedes`). The atomizer MUST assign `slice_id`, `artifact_kind`, `checksum`, and `supersedes` deterministically and MUST NOT extend or redefine the Stage 3 required frontmatter schema. `checksum` MUST equal `sha256(slice body)`.

#### Scenario: Slice passes Stage 3 gate

- **WHEN** a knowledge slice produced by the atomizer is fed to `python3 -m paulshaclaw.lifecycle.gate`
- **THEN** validation MUST pass

#### Scenario: Invalid slice fails closed

- **WHEN** a fragment cannot be mapped to a valid `artifact_kind` and a slice would fail frontmatter validation
- **THEN** the slice MUST NOT be written to `knowledge/`
- **THEN** the session MUST remain in `state=split` and a warning MUST be logged

### Requirement: LLM output contract and slice assembly

Stage 2 SHALL require the LLM promoter to return a JSON array of slice proposals, each with `title`, `artifact_kind` (a Stage 3 `ARTIFACT_KIND`), `project` (a known project or `_unknown`), `tags`, a non-empty `body`, `source_fragment_indices`, and `relations`. Stage 2 MUST parse and schema-validate this output and MUST fail closed on any violation. Each accepted proposal MUST become a slice whose frontmatter is the Topic 4 ∪ Stage 3 union plus `tags`, whose `checksum` equals `sha256(body)`, and whose `slice_id` is content-derived from the session identity and body.

#### Scenario: Invalid output fails closed for the whole session

- **WHEN** the agent returns malformed JSON, an out-of-range `artifact_kind`, an unknown `project`, or an empty `body`
- **THEN** no knowledge slice MAY be written for that session
- **THEN** the session MUST remain in `state=split` with a warning, for retry on the next run

### Requirement: Object-wrapped JSON array unwrap in promotion parsing

The atomizer JSON extraction SHALL unwrap a top-level JSON object only when it contains exactly one top-level key, that key is drawn from the whitelist {`findings`, `slices`, `proposals`, `atoms`}, and its value is a list, treating that list as the proposals payload. Extraction of a bare top-level array and of multiple valid arrays MUST continue to behave exactly as before this change.

#### Scenario: Object-wrapped non-empty array extracts to slices

- **WHEN** the model returns `{"findings": [ {…}, {…} ]}`
- **THEN** the parser unwraps the `findings` array and extracts the two slice proposals

#### Scenario: Object-wrapped empty array reaches the slices=0 terminal state

- **WHEN** the model returns `{"findings": []}`
- **THEN** the parser yields an empty array and the session reaches the `slices=0` terminal state (promoted, fragments archived, cache cleared) rather than being parked

#### Scenario: Bare array and multiple arrays are unchanged

- **WHEN** the output is a bare top-level JSON array, or contains multiple valid top-level arrays
- **THEN** extraction behaves identically to the pre-change parser

#### Scenario: Object with multiple, non-whitelisted, or extra top-level fields is not unwrapped

- **WHEN** the top-level object has more than one top-level key, has more than one array-valued key, or its lone array key is not in the whitelist
- **THEN** the parser does NOT unwrap it (no false-positive extraction)

### Requirement: Flow-through with archive retention

Stage 2 SHALL keep working layers lean by moving consumed inputs out of the working layer into `archive/`, not by deleting them. After `split_pass`, the raw session MUST be moved to `archive/sessions/<YYYY-MM>/`. After `promote_pass`, the fragments MUST be moved to `archive/fragments/<YYYY-MM>/`. The raw layer MUST NOT retain processed sessions and `inbox/_slices/` MUST NOT retain promoted fragments, while the original content MUST remain recoverable under `archive/`.

#### Scenario: Working layers are emptied, archive retains evidence

- **WHEN** a session has completed both passes
- **THEN** the raw layer MUST NOT contain that session and `inbox/_slices/` MUST NOT contain its fragments
- **THEN** `archive/sessions/` and `archive/fragments/` MUST contain the consumed inputs

### Requirement: Semantic relations and tags

Stage 2 SHALL record LLM-inferred semantic relations as new edge types in `runtime/ledger/relations.jsonl`: `relates_to` (slice to slice) and `mentions` (slice to `entity:<NAME>`), in addition to the deterministic derivation edges. A `relates_to` whose target cannot be resolved within the promotion batch MUST be skipped with a warning rather than failing the session. Slice tags MUST be stored in the Stage-2-owned `tags` frontmatter field and MUST NOT extend the Stage 3 required schema.

#### Scenario: Dangling relation is skipped, not fatal

- **WHEN** a proposal's `relates_to` target title does not match any slice in the same batch
- **THEN** that edge MUST be skipped with a warning
- **THEN** the remaining slices and edges MUST still be written

### Requirement: Processing ledger and relations

Stage 2 SHALL record every processed session in an append-only processing ledger at `runtime/ledger/processing.jsonl` keyed by `<agent>:<session>`, with states `split` (deterministic analysis done, in process) and `promoted` (atomized, processed). A session with no ledger entry MUST be treated as not-yet-processed. Stage 2 SHALL also record derivation and semantic edges in an append-only `runtime/ledger/relations.jsonl` with edge types `fragment_of`, `promoted_to`, `distilled_from`, `supersedes`, `relates_to`, and `mentions`, with nodes namespaced `session:`/`fragment:`/`slice:`. Both ledgers MUST stamp each record with the injected scan `now` (not wall-clock) and the `atomizer_config_hash`, MUST be append-only, and MUST NOT store raw record body content. The `promoted` ledger record for LLM promotion MUST include `promoter`, `model`, and `skill_hash`.

#### Scenario: Processing state is queryable

- **WHEN** a session has been split but not promoted
- **THEN** the processing ledger fold MUST report its state as `split`

#### Scenario: Slice traces back to its session

- **WHEN** a knowledge slice exists
- **THEN** `relations.jsonl` MUST contain a `distilled_from` edge from that slice to its origin `session:<agent>:<sid>`

#### Scenario: Promoted record is traceable

- **WHEN** the LLM promoter promotes a session
- **THEN** the `processing.jsonl` `promoted` record MUST include `promoter`, `model`, and `skill_hash`

### Requirement: Frozen LLM output and crash-resume determinism

Stage 2 SHALL freeze a session's LLM output at first promotion so the non-deterministic step is idempotent on resume. The raw agent output MUST be cached at `runtime/cache/atomize/<session_key>__<fragments_hash>.json`; a subsequent promotion of the same session and fragments MUST reuse the cache rather than re-invoking the agent. A corrupt cache entry MUST be treated as a miss (re-invoke) and MUST NOT fail the run. Successful promotion MUST clear the session cache after archiving fragments.

#### Scenario: Resume reuses cached output

- **WHEN** a promotion is interrupted after the agent call but before the `promoted` ledger record
- **THEN** the next run MUST reuse the cached agent output and MUST NOT call the agent again for that session

### Requirement: Deterministic atomizer execution and fail modes

Stage 2 atomizer execution MUST be deterministic given `(records, ledgers, config, now)` once any LLM output for a session has been frozen in cache: no randomness beyond the first agent invocation, injected `now`, and a deterministic `atomizer_config_hash` over the effective config. Config load failure or unsupported `schema_version` MUST fail closed (abort, no writes). A corrupt `processing.jsonl` or `relations.jsonl` line MUST fail closed for the affected pass. A single unparseable raw session MUST be skipped with a warning without aborting the run. Any promoter failure or schema violation during promotion MUST leave the session in `state=split`.

#### Scenario: Unsupported config version fails closed

- **WHEN** `atomizer.yaml` declares an unsupported `schema_version`
- **THEN** the atomizer MUST abort without writing fragments, slices, or ledger entries

#### Scenario: One bad session does not abort the run

- **WHEN** a single raw session document has unparseable frontmatter
- **THEN** that session MUST be skipped with a warning
- **THEN** other sessions MUST still be processed

### Requirement: Recovery note for parser-recoverable parked sessions

The documented recovery for parser-recoverable parked sessions SHALL clear only the affected session+fragment cache key and its `.retries` sidecar, then rerun dream and expect the session to reach `promoted` (including the `slices=0` case).

#### Scenario: Recovery note scopes the cleanup and expected outcome

- **WHEN** a reviewer reads the recovery note for object-wrapped parked sessions
- **THEN** it MUST limit cleanup to the affected session+fragment cache key and matching `.retries` sidecar
- **THEN** it MUST state that rerun is expected to end in `promoted`, including the `slices=0` terminal case

### Requirement: Redaction trust boundary for distilled output

Stage 2 SHALL preserve the raw-to-distilled trust boundary even after atomization. Distilled knowledge slices MUST derive only from redacted inbox material, and any downstream Stage 2 orchestration, replay, or bundle surface MUST NOT re-read raw queue, inbox, or archive payloads to reconstruct content.

#### Scenario: Distilled output does not reopen raw inputs

- **WHEN** Stage 2 dream or bundle workflows process knowledge slices
- **THEN** they MUST use only distilled slices and ledger state
- **THEN** they MUST NOT re-open raw queue, inbox, or archive payloads

### Requirement: Dream orchestration service

Stage 2 SHALL provide an independent `dream` service that orchestrates the existing per-pass components rather than reimplementing them. `psc memory dream run` MUST execute the Topic 3/3.2 atomize pass and then the Topic 4 janitor pass over the current backlog, in that order, and MUST record one run record to `runtime/ledger/dream.jsonl`. The two passes MUST be isolated: a failure in one pass MUST be recorded and MUST NOT prevent the other pass from running or crash the run. The service MUST be separate from the ingestion pipeline and MUST be triggered by a scheduler, never by the importer.

#### Scenario: Passes run in order and are isolated

- **WHEN** `dream run` executes and the atomize pass raises
- **THEN** the janitor pass MUST still run
- **THEN** the run record MUST capture the atomize error and a degraded `status`

#### Scenario: Run is recorded

- **WHEN** a non-dry-run `dream run` completes
- **THEN** `runtime/ledger/dream.jsonl` MUST gain one record with `run_id`, `status` (`ok`/`partial`/`failed`), per-pass summaries, and `dream_config_hash`

#### Scenario: Dry run does not mutate state

- **WHEN** `dream run --dry-run` executes
- **THEN** both passes MUST run in dry mode and no `dream.jsonl` record MUST be written

### Requirement: Dream status and backlog

Stage 2 SHALL provide `psc memory dream status` returning the latest run summary (from `dream.jsonl`) and the current backlog depth (count of unprocessed raw sessions). The status MUST NOT contain slice body content or any raw prompt content.

#### Scenario: Status reflects last run

- **WHEN** `dream status` runs after a `dream run`
- **THEN** it MUST report the latest run's `status` and a backlog depth

### Requirement: Idle-gated scheduling

Stage 2 SHALL ship a systemd user unit/timer template and an idle-check wrapper so the dream service can run on a recurring schedule only when the system is idle. The timer template MUST use `OnCalendar` on a recurring schedule (the concrete cadence is governed by the `dream-resource-governance` capability) and MUST invoke `dream run --require-idle`. `--require-idle` MUST skip the run (log and exit zero) when the system is confirmed busy, and MUST proceed when idle or when idleness cannot be determined (fail-safe-to-run). The idle decision MUST be implemented in Python so it is unit-testable with an injected probe.

#### Scenario: Busy system skips

- **WHEN** `dream run --require-idle` runs and the idle probe reports busy
- **THEN** the run MUST be skipped, exit zero, and write no `dream.jsonl` record

#### Scenario: Indeterminate idleness proceeds

- **WHEN** the idle probe cannot determine load
- **THEN** the run MUST proceed

### Requirement: Proposal-first framework for cross-session changes

Stage 2 SHALL establish a proposal-first framework at `runtime/proposals/` with a human-review gate, so that any future cross-session canonical change (lineage merges, supersession, entity reconciliation) is proposed and gated rather than auto-applied. The MVP dream service MUST NOT auto-apply any cross-session canonical change and MUST NOT generate proposal content in this change; the framework API (`append`, `pending`, `requires_approval`) and storage MUST exist for later population.

#### Scenario: No auto-apply path

- **WHEN** the MVP dream service runs
- **THEN** it MUST NOT write any cross-session merge/supersession directly to `knowledge/`
- **THEN** `requires_approval` MUST return true for canonical-mutating proposal kinds

### Requirement: Replay bundle reads only distilled artefacts and ledger

Stage 2 SHALL provide `psc memory bundle` that assembles a replay bundle from selected knowledge slices and their ledger events, and MUST NOT read raw prompts or raw queue/inbox/archive payloads. Selection MUST support `--project`, `--tag`, and `--entity` facets combined with AND, default to the active (non-decayed) set via the Topic 4 retrieval-set API, and require at least one facet. The bundle MUST contain a `manifest.json` (selection, slice ids, counts, `raw_excluded: true`), a `slices/` directory of the selected distilled slices, and a `ledger.jsonl` of the touching lifecycle/relations/processing events.

#### Scenario: Bundle excludes raw

- **WHEN** a bundle is built for any selection
- **THEN** the bundle MUST contain only distilled slices and ledger events
- **THEN** `manifest.json` MUST declare `raw_excluded: true` and the bundle MUST contain no raw prompt content

#### Scenario: At least one facet required

- **WHEN** `bundle` is invoked with no `--project`/`--tag`/`--entity`
- **THEN** it MUST fail with an error rather than dumping the whole knowledge base

#### Scenario: Active-set default

- **WHEN** a bundle is built without `--include-decayed`
- **THEN** decayed slices MUST be excluded via the Topic 4 retrieval-set API

### Requirement: Dream service determinism and logs

Stage 2 dream and bundle execution MUST inject `now` (no wall-clock in records), append-only with flock for `dream.jsonl`, and MUST NOT write slice body content or raw content to logs (`~/.agents/memory/log/dream.log` carries only run id, counts, and error categories). A corrupt `dream.jsonl` line MUST fail closed on read.

#### Scenario: Logs carry no content

- **WHEN** the dream service logs a pass failure
- **THEN** the log entry MUST contain only the run id, counts, and error category, not slice or raw content

Stage 2 LLM promotion MUST treat its input fragments as already redacted at the Topic 8 `raw_to_distilled` boundary and MUST NOT re-scan slice bodies for secrets in this change. This limitation MUST be documented; a future `distilled_to_canonical` policy pass is the place to add output-side scanning. Logs MUST NOT contain raw agent output or session body content.

#### Scenario: Logs never contain model output

- **WHEN** the promoter logs a failure
- **THEN** the log entry MUST NOT contain the raw agent output or any session body content
- **THEN** it MUST contain only the failure category, `session_key`, and skill/config hashes

### Requirement: Obsidian-native relation links in slice frontmatter

Stage 2 SHALL materialize the semantic relations recorded in `runtime/ledger/relations.jsonl` into each knowledge slice as Obsidian-native links in the slice frontmatter only. Links MUST be written to a `related:` frontmatter list (and an `aliases:` entry for the readable title), and MUST NOT be written into the slice body. `relates_to` edges MUST produce bidirectional `[[<basename>]]` links between slices; `mentions` edges MUST produce `[[<entity>]]` links. The materialization MUST NOT modify the slice body, so the Stage 3 `checksum` and the content-derived `slice_id` remain unchanged. `relations.jsonl` remains the append-only source of truth; the materializer only reads it.

#### Scenario: Links never touch the body

- **WHEN** the materializer adds `related:` links to a slice
- **THEN** the slice body MUST be unchanged
- **THEN** the slice MUST still pass `paulshaclaw.lifecycle.schema.validate_frontmatter` (checksum intact) and its `slice_id` MUST be unchanged

#### Scenario: Bidirectional slice links

- **WHEN** slice A has a `relates_to` edge to slice B
- **THEN** A's `related:` MUST link B and B's `related:` MUST link A

### Requirement: Readable slice filenames keyed by slice_id

Stage 2 SHALL name knowledge slice files `<readable-title>--<slice_id>.md` so the Obsidian graph shows readable nodes while `slice_id` stays the stable identity in frontmatter. The atomizer MUST locate and overwrite an existing slice by globbing `*--<slice_id>.md` (or the legacy `<slice_id>.md`) rather than assuming a fixed filename, so re-imports overwrite in place and never create a duplicate `slice_id`. The title MUST be derived deterministically (frontmatter `title`, else first heading, else `<artifact_kind>-<project>`).

#### Scenario: Re-import overwrites, no duplicate slice_id

- **WHEN** a slice has been renamed to `<title>--<slice_id>.md` and the same session is re-imported with changed content
- **THEN** the atomizer MUST overwrite that file
- **THEN** no second file with the same `slice_id` MUST exist (the replay selector MUST NOT raise a duplicate-slice_id error)

### Requirement: MOC reconcile dedup deletions are recorded in the lifecycle ledger

When the MOC naming reconcile pass deletes a file to resolve a duplicate `slice_id` (or removes an older file it overwrites during rename), it SHALL append a lifecycle event to `runtime/ledger/lifecycle.jsonl` via the lifecycle ledger API, recording the deletion with `event_type` `superseded`, the `slice_id` as `record_id`, a `reason` identifying the moc dedup, and metadata identifying the deleted and kept paths. No reconcile deletion may be unrecorded.

The reconcile pass's choice of **which** file survives MUST remain identical to the pre-change behavior — only the ledger trace is added. These audit-only lifecycle events MUST NOT change the slice's effective lifecycle state or lifecycle-based recency semantics. A lifecycle-ledger write failure MUST NOT abort the pass; it degrades to the existing warning and the pass continues.

#### Scenario: Duplicate slice_id dedup emits a lifecycle event

- **WHEN** reconcile finds two files with the same `slice_id` and deletes the older one
- **THEN** a lifecycle event recording the deletion (`slice_id`, deleted path, kept path, reason) is appended to the lifecycle ledger, and the surviving file is the same one reconcile would have kept before this change

#### Scenario: Ledger write failure does not abort the pass

- **WHEN** the lifecycle ledger append raises during a reconcile dedup
- **THEN** reconcile still completes the pass (degrading to the existing warning) and does not propagate the error

#### Scenario: Deletion selection is unchanged

- **WHEN** reconcile dedups duplicate `slice_id`s
- **THEN** which file is deleted versus kept is identical to the behavior before this change (the ledger trace is purely additive)

### Requirement: Three MOC index files

Stage 2 SHALL generate three Maps-of-Content under `knowledge/` per the original memory design: a `<project>-moc.md` per project, a `common-sense-moc.md`, and a `wiki-moc.md` global index. Each MOC file MUST carry `memory_layer: moc` frontmatter. MOC files MUST list only active (non-decayed) slices in their active sections, link to them by basename, and MUST NOT themselves be treated as knowledge records.

#### Scenario: MOC files are excluded from knowledge-record scanners

- **WHEN** the janitor record source or the replay selector scans `knowledge/`
- **THEN** files with `memory_layer: moc` MUST be skipped and MUST NOT be treated as slices

#### Scenario: Wiki MOC indexes all active slices

- **WHEN** `wiki-moc.md` is generated
- **THEN** it MUST list every active slice under an active section

### Requirement: Faceout surfacing in wiki MOC

Stage 2 SHALL surface decayed (faceout) knowledge in `wiki-moc.md` under a dedicated faceout section listing the decayed slice, its decay reason, and the event time, sourced from the lifecycle ledger. Faceout MUST NOT delete the slice file and MUST NOT mutate the slice's own frontmatter; the lifecycle ledger remains the source of truth.

#### Scenario: Decayed slice is surfaced, not deleted

- **WHEN** a slice is decayed
- **THEN** `wiki-moc.md` MUST list it under the faceout section with its reason
- **THEN** the slice file MUST still exist under `knowledge/`

### Requirement: Lexical search index and query

Stage 2 SHALL build a SQLite FTS5 lexical index of knowledge slices at `runtime/indexes/retrieval.db` and expose `psc memory search`. The index MUST cover slice title, tags, body, and project, plus per-slice metadata (`captured_at`, active flag, and a bidirectional-link `link_weight`). Queries MUST support project scoping, default to the active set (excluding decayed unless `--include-decayed`), and rank by a deterministic weighted combination of BM25, recency, and `link_weight`. A missing index MUST produce an actionable error rather than silently returning nothing.

#### Scenario: Search is project-scopable and active by default

- **WHEN** `psc memory search "<q>" --project P` runs
- **THEN** results MUST be limited to project P and MUST exclude decayed slices unless `--include-decayed` is set

#### Scenario: Missing index errors clearly

- **WHEN** `psc memory search` runs with no index present
- **THEN** it MUST report that the index is not built (run the dream/moc pass) rather than return an empty result silently

### Requirement: MOC materialization runs as an isolated, deterministic dream pass

Stage 2 SHALL run the MOC materialization (rename, link, MOC build, faceout, index) as a third dream pass after atomize and janitor: `atomize → janitor → moc`. The pass MUST be deterministic (inject `now`, no LLM), idempotent (a re-run reproduces the same `related:`/MOC/index), and isolated (a failure is recorded and MUST NOT block the other passes or crash the run). It MUST only touch `knowledge/` and MUST NOT couple to or invoke `obs-auto-moc`.

#### Scenario: MOC pass runs after janitor and is isolated

- **WHEN** the dream service runs
- **THEN** the moc pass MUST run after the janitor pass
- **THEN** a failure in the moc pass MUST be recorded in the dream run record without crashing the run

#### Scenario: Re-run is idempotent

- **WHEN** the moc pass runs twice over unchanged inputs
- **THEN** the produced `related:` links, MOC files, and index MUST be identical

### Requirement: Gate-protected atomize-skill optimization

Stage 2 SHALL provide a `paulshaclaw/memory/skillopt/` code module that refines the atomizer's `atomize-knowledge-slice.md` SKILL using a vendored copy of the `evolve` generic SkillOpt loop. The module MUST treat the SKILL as a trainable artifact and MUST only overwrite it when a candidate scores strictly higher than the baseline on the validation set and is a structurally valid skill; otherwise the original SKILL MUST remain unchanged. The vendored loop MUST preserve the upstream behavior (validation gate, fail-closed sanitized errors, pre-write backup to `skillopt-history/`, records limited to scores/counts/decision). The module MUST be a code module, not a skill, and MUST run as an offline CLI that is NOT wired into the dream loop in this change.

#### Scenario: Candidate that does not improve is rejected

- **WHEN** `psc memory skillopt run` produces a candidate whose mean validation score is not strictly greater than the baseline
- **THEN** `atomize-knowledge-slice.md` MUST remain byte-identical to its pre-run content
- **THEN** the run result reason MUST be `rejected: no improvement`

#### Scenario: Improving candidate is accepted with backup

- **WHEN** a candidate scores strictly higher than baseline on the validation set and is a valid skill
- **THEN** the prior skill MUST be backed up under `skillopt-history/<skill_stem>/<ts>.md` before the overwrite
- **THEN** `atomize-knowledge-slice.md` MUST be updated to the candidate
- **THEN** an append-only record limited to scores/counts/decision MUST be written to `runtime/ledger/skillopt.jsonl`

#### Scenario: Model failure leaves the skill unchanged

- **WHEN** the rollout (gemma4), optimizer (codex), or judge model times out or raises
- **THEN** the run MUST fail closed with reason `error`, returning no partial scores
- **THEN** `atomize-knowledge-slice.md` MUST remain unchanged

### Requirement: Reuse importer and atomizer without duplication

The SkillOpt module SHALL NOT re-implement session scanning or project resolution; it MUST consume importer-produced inbox fragments and the `project` value already present in their frontmatter. The atomize rollout MUST reuse the existing `build_prompt` + `LLMPromoter` by injecting the candidate `skill_text`, and MUST NOT fork a separate atomization splitter. Cross-folder-same-project identity MUST be taken from the importer's `project_resolver` (driven by `projects.yaml` roots/remotes) and MUST NOT be delegated to the LLM judge.

#### Scenario: Project comes from importer, not the judge

- **WHEN** the val_set builder assigns a project to a fragment
- **THEN** it MUST read the `project` field written by the importer
- **THEN** the LLM judge MUST NOT be asked to assign or correct the project

#### Scenario: Rollout injects the candidate skill

- **WHEN** the loop evaluates a candidate `skill_text`
- **THEN** that exact `skill_text` MUST be the skill passed into `build_prompt` for the atomize rollout

### Requirement: Project-stratified deterministic validation set with reference-only `~/notes`

The val_set builder SHALL stratify items by `project` and split each project's items into train/validation deterministically, such that identical inputs always yield identical splits (e.g. via a hash of `"<session_id>#<fragment_index>"`), with no wall-clock or random source. A project with fewer than the configured minimum sample size MUST contribute all its items to train and none to validation, and the downgrade MUST be logged. The `~/notes` Obsidian vault MUST be used read-only as reference exemplars (semantic content only, frontmatter ignored) supplied to the judge as a rubric; it MUST NOT be used as a paired gold target, MUST NOT be written, and `PersonalVault` MUST be excluded.

#### Scenario: Deterministic split is reproducible

- **WHEN** `build_valset` runs twice over the same inbox content
- **THEN** the train/validation partitions MUST be identical across runs

#### Scenario: Sparse project avoids noisy validation

- **WHEN** a project has fewer items than the configured minimum sample size
- **THEN** all of that project's items MUST go to train and none to validation
- **THEN** the downgrade MUST be logged

#### Scenario: `~/notes` is reference only

- **WHEN** the builder or judge accesses `~/notes`
- **THEN** access MUST be read-only and limited to semantic content used as judge rubric
- **THEN** `PersonalVault` MUST NOT be read
- **THEN** no run MUST treat a `~/notes` note as a 1:1 gold target

### Requirement: LLM judge scores atomization quality only

The validation gate SHALL score each rollout output with a hybrid of a deterministic structural score and an LLM judge, combined as a weighted sum that yields an absolute 0–1 score per output (preserving the generic loop's `score(output, gold)` contract). The structural score MUST be used for train-failure ranking. The judge MUST evaluate atomization quality only — slice granularity, concept boundary, one-concept-per-slice, and relation soundness — and MUST NOT evaluate project assignment.

#### Scenario: Structural score ranks train failures

- **WHEN** the loop selects failures from the train set
- **THEN** it MUST rank by the deterministic structural score (no LLM call required)

#### Scenario: Hybrid score gates validation

- **WHEN** the loop scores a candidate on the validation set
- **THEN** the score MUST combine structural and judge components into a single 0–1 value
- **THEN** the judge MUST NOT be prompted to assign or correct the project

### Requirement: Promotion failure clears the poisoned LLM cache

When session promotion fails with a `PromoteError` under an LLM promoter backed by a caching agent client, the atomizer pipeline SHALL clear the cached raw output for that session's fragments (cache key `<agent>:<session>__<sha256(fragments)>`) before leaving the session in `split` state **only when that failed attempt actually produced cached raw output**. Failures that occur before any cache write (for example an underlying `AgentExecError` transport failure) MUST leave the retry counter unchanged, MUST NOT claim poisoned-cache retention, and MUST keep the session eligible for a real LLM call on the next atomize run. Cache clearing MUST NOT occur for non-LLM promoters, MUST NOT occur in dry-run mode, and MUST only unlink files inside `runtime/cache/atomize/` after validating the cache key.

#### Scenario: Poisoned cache is cleared on promote failure

- **WHEN** an LLM promoter with a caching agent client produces unparseable output for a split session (a `PromoteError`)
- **THEN** the session MUST remain in `split` state with a `left in split` warning
- **THEN** the cache file for that session's fragments MUST no longer exist under `runtime/cache/atomize/`

#### Scenario: Next run re-invokes the LLM and recovers

- **WHEN** the first atomize run fails promotion with bad LLM output and a second run's LLM output is valid
- **THEN** the second run MUST invoke the underlying agent again (no cache replay of the bad output)
- **THEN** the session MUST reach `promoted` state on the second run

#### Scenario: Transport failure leaves no cache and no retry-budget mutation

- **WHEN** the underlying agent command fails before any raw output is cached for a split session
- **THEN** no `.json` cache file may be created and no `.retries` sidecar may be created or incremented for that failed attempt
- **THEN** the warning text MUST describe a transport / no-cache retry state and MUST NOT claim poisoned-cache retention
- **THEN** a later run MUST still invoke the underlying agent again

#### Scenario: Identity promoter failures do not touch the cache

- **WHEN** a non-LLM promoter raises during promotion for a session
- **THEN** no file under `runtime/cache/atomize/` may be created or deleted by the failure handling

#### Scenario: Dry-run backlog preview does not mutate cache or retry budget

- **WHEN** `dry_run=True`, the raw inbox document for a session has already been archived, and that session remains in `split` with an existing cache file and `.retries` sidecar
- **THEN** the cache file MUST remain present and the `.retries` sidecar MUST remain unchanged after the dry-run
- **THEN** the underlying agent MUST NOT be invoked

### Requirement: Empty proposal output is a terminal promoted state

The LLM output parser SHALL treat an empty JSON array (including one wrapped in a fenced code block and surrounded by prose) as a valid result meaning "no extractable knowledge", returning zero proposals instead of raising. The atomizer pipeline SHALL bring such a session to `state=promoted` with `slices=0`, archive its fragments, clear its cache, and emit no `left in split` warning. A non-empty JSON array whose every proposal is schema-invalid MUST still raise (`no salvageable proposals`).

#### Scenario: Bare empty array parses to zero proposals

- **WHEN** the raw agent output is `[]`
- **THEN** parsing MUST return an empty proposal list without raising

#### Scenario: Fenced empty array with reasoning prose parses to zero proposals

- **WHEN** the raw agent output is a fenced ```json``` block containing `[]` followed by reasoning prose
- **THEN** parsing MUST return an empty proposal list without raising

#### Scenario: Session with empty LLM output reaches promoted with zero slices

- **WHEN** an LLM promoter returns zero slices for a split session
- **THEN** the processing ledger MUST record `state=promoted` with `slices: 0` for that session
- **THEN** the session's fragments MUST be archived out of `inbox/_slices/` and its cache cleared
- **THEN** no `left in split` warning may be emitted for that session

#### Scenario: All-invalid proposals still fail closed

- **WHEN** the raw agent output is a non-empty JSON array in which every proposal violates the schema
- **THEN** parsing MUST raise an error mentioning `no salvageable proposals`

### Requirement: Bounded LLM retry budget per stuck session

The atomizer pipeline SHALL bound LLM re-invocations for a repeatedly **content-failing** session using a persistent retry counter stored as `runtime/cache/atomize/<cache_key>.retries`. On each `PromoteError` under an LLM promoter (non-dry-run), the counter MUST be incremented only when the cached raw-output file exists for that failed attempt; transport failures that leave no cached raw output MUST leave the counter unchanged. The poisoned cache MUST be cleared only while the incremented count is at or below the budget (5). Once the count exceeds the budget, the poisoned cache MUST be retained so later runs fail at parse time without invoking the LLM, while still emitting a warning that identifies the exhausted budget. Successful promotion MUST remove the retry counter together with the cache file. Retry-counter file paths MUST pass the same cache-key validation and directory-containment checks as cache files.

#### Scenario: Failure within budget increments the counter and clears the cache

- **WHEN** a split session fails promotion for the first time under an LLM promoter
- **THEN** `runtime/cache/atomize/<cache_key>.retries` MUST contain `1`
- **THEN** the cache file MUST be cleared so the next run re-invokes the LLM

#### Scenario: First post-outage content failure starts at retry 1

- **WHEN** one or more transport failures happened without writing cached raw output and a later LLM response fails after being cached
- **THEN** `runtime/cache/atomize/<cache_key>.retries` MUST contain `1`
- **THEN** the cache file MUST be cleared so the session still retains the remaining content retry budget

#### Scenario: Exhausted budget parks on the poisoned cache

- **WHEN** a session's retry counter already equals the budget and promotion fails again
- **THEN** the counter MUST be incremented past the budget and the cache file MUST be retained
- **THEN** a subsequent run MUST NOT invoke the underlying agent for that session (cache replay only) and MUST still record a warning

#### Scenario: Success clears the retry counter

- **WHEN** a session with an existing retry counter is successfully promoted
- **THEN** both the cache file and the `.retries` sidecar for its cache key MUST be removed

### Requirement: Dream record surfaces pass warnings

The dream orchestrator SHALL include warning text in the per-pass summary written to the dream ledger: when a pass returns a non-empty warnings list, the pass summary MUST contain `warnings` (the first 10 warning strings, each truncated to at most 500 characters) and `warnings_total` (the full count). When the warnings list is empty or absent, the pass summary MUST NOT contain these keys. Recorded warning text MUST NOT include raw prompts or raw LLM output bodies.

#### Scenario: Warning text reaches the dream ledger

- **WHEN** the atomize pass returns warnings `["claude:s1: llm promote failed: ...; session claude:s1 left in split"]`
- **THEN** the persisted dream record MUST contain that warning string in `passes.atomize.warnings`
- **THEN** `passes.atomize.warnings_total` MUST equal `1` and the run status MUST be `partial`

#### Scenario: Warning overflow is truncated but counted

- **WHEN** a pass returns 45 warnings
- **THEN** `passes.<pass>.warnings` MUST contain exactly 10 entries and `warnings_total` MUST equal `45`

#### Scenario: Clean pass summary is unchanged

- **WHEN** a pass returns an empty warnings list
- **THEN** its pass summary MUST NOT contain `warnings` or `warnings_total` keys

### Requirement: Atomizer default promoter is LLM distillation

Stage 2 SHALL ship the atomizer with `promoter: llm` as the packaged default (`paulshaclaw/memory/atomizer/atomizer.yaml`). Any CLI path invoked without an explicit `--promoter` flag (`memory atomize`, `memory dream run`) MUST resolve to the LLM promoter and MUST NOT construct an `IdentityPromoter`. The identity promoter MUST remain available as an explicit `--promoter identity` option for tests and offline deterministic runs. The code-level fallback for configs that omit the `promoter` key entirely MUST remain `identity` (fail-safe: a stripped-down config never silently upgrades into spawning an external LLM call).

#### Scenario: Packaged config default resolves to llm

- **WHEN** `atomizer.config.load_config(override_path=None)` loads the packaged `atomizer.yaml`
- **THEN** the resulting `AtomizerConfig.default_promoter` MUST equal `"llm"`

#### Scenario: CLI without --promoter builds the LLM promoter

- **WHEN** `atomizer.cli._build_promoter` is called with `args.promoter = None` against the packaged config
- **THEN** the returned promoter MUST be an `LLMPromoter` instance
- **THEN** it MUST NOT be an `IdentityPromoter` instance

#### Scenario: Explicit identity flag is still honored

- **WHEN** `atomizer.cli._build_promoter` is called with `args.promoter = "identity"`
- **THEN** the returned promoter MUST be an `IdentityPromoter` instance

#### Scenario: Config omitting the promoter key fails safe to identity

- **WHEN** `load_config` reads a config file that contains no `promoter` key
- **THEN** `default_promoter` MUST resolve to `"identity"`
- **THEN** no LLM agent process MAY be spawned as a side effect of loading configuration

### Requirement: Scheduled dream templates pin the LLM promoter

The repo-shipped schedule templates (`paulshaclaw/memory/dream/scripts/dream-idle-wrapper.sh` and `paulshaclaw/memory/dream/systemd/paulsha-memory-dream.service`) SHALL pin `--promoter llm` explicitly, matching the production dream loop (`scripts/start.sh`). Each template MUST carry a comment documenting the identity promoter's boilerplate-output risk: identity copies importer template fragments 1:1 into knowledge slices and the noise gate only drops part of that boilerplate.

#### Scenario: Wrapper pins llm

- **WHEN** an operator inspects `dream-idle-wrapper.sh`
- **THEN** its `memory dream run` invocation MUST contain `--promoter llm`
- **THEN** it MUST NOT contain `--promoter identity`
- **THEN** a comment MUST explain the identity boilerplate risk

#### Scenario: Systemd service pins llm

- **WHEN** an operator inspects `paulsha-memory-dream.service`
- **THEN** its `ExecStart` MUST contain `--promoter llm`
- **THEN** it MUST NOT contain `--promoter identity`

#### Scenario: Enabling the systemd timer does not reintroduce identity promotion

- **WHEN** the (currently uninstalled) systemd timer/service pair is enabled without modification
- **THEN** every scheduled dream run MUST use the LLM promoter

### Requirement: Project key rekey migration

Stage 2 SHALL 提供一次性 rekey 遷移工具：模組 `paulshaclaw.memory.rekey` 與 CLI `memory knowledge rekey --memory-root <root> --from <old-key> --to <slug>`。工具 MUST 僅選取 frontmatter `memory_layer: knowledge` 且 `project` 與 `<old-key>` 嚴格相等的 slice（略過 `-moc.md`）。`--to` MUST 為 path-safe slug（依 `atomizer/config.py::is_safe_path_component`，不得含 `/`），違反時 CLI MUST 以 exit code 2 拒絕且不產生任何副作用。預設（未帶 `--apply`）為 dry-run：MUST 產出審計 manifest `runtime/ledger/rekey-<now>.jsonl`（原子寫入）且 MUST NOT 改動任何 knowledge 檔案。`--apply` 時每筆候選 MUST 改寫 frontmatter `project` 為新 slug、將檔案搬移至 `knowledge/<sanitized-slug>/`（`sanitize_project_component`），並保留 `slice_id` 與 body；有任何成功筆數時 MUST 觸發 `moc/runner.py::run_moc` 重建 MOC 與 retrieval index。工具 MUST NOT 直接讀寫 `runtime/indexes/retrieval.db`。

#### Scenario: dry-run 產 manifest 不動檔案

- **WHEN** 對含 1 筆 `project: github.com/hamanpaul/testpilot` slice 的 memory root 執行 rekey（`--to testpilot`，未帶 `--apply`）
- **THEN** `runtime/ledger/rekey-<now>.jsonl` MUST 存在且該筆 status 為 `dry-run`、含 `from`/`to`/`path`/`target` 欄位
- **THEN** 原檔案 MUST 原地保留且 frontmatter `project` 不變

#### Scenario: apply 搬檔、改 frontmatter、重建 MOC

- **WHEN** 對同一 memory root 執行 rekey `--apply`
- **THEN** 檔案 MUST 出現在 `knowledge/testpilot/` 下且原路徑消失
- **THEN** frontmatter `project` MUST 等於 `testpilot`，`slice_id` 與 body MUST 逐字不變
- **THEN** manifest 該筆 status MUST 為 `rekeyed`，且 `knowledge/testpilot-moc.md` MUST 存在（run_moc 已觸發）

#### Scenario: 目標檔已存在時 conflict fail-safe

- **WHEN** 目的地 `knowledge/<slug>/` 已存在同名檔案
- **THEN** 該筆 MUST 記為 `conflict`，source 檔案（含 frontmatter）MUST 完全不動

#### Scenario: 不安全 slug 被拒絕

- **WHEN** `--to` 含 `/`（如 `a/b`）
- **THEN** CLI MUST 回傳 exit code 2 且 MUST NOT 產生 manifest 或改動任何檔案

#### Scenario: apply 收尾清空的舊 key 目錄與孤兒 moc

- **WHEN** apply 成功搬走舊 key 目錄下全部檔案，且 `knowledge/<sanitized-old-key>-moc.md` 存在
- **THEN** 清空的 `knowledge/<sanitized-old-key>/` 目錄 MUST 被移除
- **THEN** 孤兒 `<sanitized-old-key>-moc.md` MUST 被移除

### Requirement: Fixed-list prune mode

`memory knowledge prune-noise` SHALL 支援 `--paths <file>` 固定清單模式：清單檔每行一個絕對路徑，`#` 開頭與空白行忽略。此模式 MUST 與 `--instruction-root`、`--project` 互斥（同時給定 → exit code 2、零副作用）。刪除範圍 MUST 恰為清單內檔案——清單即權威，不需 `classify_noise` 同意，manifest reason MUST 為 `listed`。驗證 MUST fail-closed：任一清單路徑不存在、resolve 後不在 `<memory-root>/knowledge/` 之下、為 `-moc.md`、或 frontmatter 非 `memory_layer: knowledge` 時，MUST 以 exit code 2 中止且 MUST NOT 刪除任何檔案。清單全部有效時 MUST 在任何 unlink 之前先寫 manifest，apply 後更新各筆狀態並重建 MOC（`build_mocs`）。

#### Scenario: 只刪清單內檔案

- **WHEN** 清單僅含 1 筆 untitled slice 路徑，而同專案另有 1 筆可判 noise 但未列清單的 slice，執行 `--paths <file> --apply`
- **THEN** 清單內檔案 MUST 被刪除（即使 `classify_noise` 不會判它為 noise）
- **THEN** 未列清單的 noise 檔與其他真筆記 MUST 全部保留
- **THEN** manifest MUST 恰含清單筆數列、reason 全為 `listed`

#### Scenario: 清單超出範圍即整批中止

- **WHEN** 清單含一個不存在的路徑或一個位於 `knowledge/` 之外的檔案
- **THEN** 命令 MUST 回傳 exit code 2
- **THEN** 清單內其餘（本身有效的）檔案 MUST NOT 被刪除

#### Scenario: dry-run 不刪

- **WHEN** 以 `--paths <file> --dry-run` 執行
- **THEN** manifest MUST 產出且各筆 status 為 `dry-run`，所有檔案 MUST 保留

#### Scenario: 與掃描模式互斥

- **WHEN** 同時給定 `--paths` 與 `--project`（或 `--instruction-root`）
- **THEN** 命令 MUST 回傳 exit code 2 且 MUST NOT 產生 manifest

### Requirement: Janitor hygiene lint for untitled and raw-remote keys

janitor scan SHALL 對 knowledge records 執行 read-only lint：frontmatter `title` 等於 `untitled` → rule `title-untitled`；frontmatter `project` 含 `/`（raw-remote key）→ rule `raw-remote-key`。lint MUST NOT 修改任何檔案、MUST NOT 寫入 lifecycle 事件（告警不自動改）。`run_scan` 回傳的 summary MUST 含 `lint` 欄位 `{"untitled": <N>, "raw_remote_key": <M>}`（經 dream orchestrator 的 summary passthrough 落入 dream ledger `passes.janitor`），且每筆 finding MUST 以 `lint:<rule>: <path> (project=<key>)` 形式 append 至 warnings。lint 結果 MUST deterministic（按 record_id 排序）。乾淨樹 MUST 回傳零 counts 且無 `lint:` 開頭的 warnings。

#### Scenario: untitled 與 raw-remote key 同時告警

- **WHEN** knowledge 樹含 1 筆 `title: untitled` 且 `project: github.com/hamanpaul/testpilot` 的 slice，執行 janitor scan
- **THEN** `summary["lint"]` MUST 等於 `{"untitled": 1, "raw_remote_key": 1}`
- **THEN** warnings MUST 含 2 筆 `lint:` 開頭的訊息
- **THEN** 該 slice 檔案 MUST 原封不動、lifecycle ledger MUST 無 lint 相關事件

#### Scenario: 乾淨樹零告警

- **WHEN** knowledge 樹所有 slice 都有真標題且 project 為短 slug
- **THEN** `summary["lint"]` MUST 等於 `{"untitled": 0, "raw_remote_key": 0}` 且無 `lint:` warnings

#### Scenario: 告警進 dream ledger

- **WHEN** dream run 的 janitor pass 掃到 lint findings
- **THEN** dream ledger 該輪記錄的 `passes.janitor.lint` MUST 帶有非零 counts（經既有 summary passthrough，無需 orchestrator 改動）

