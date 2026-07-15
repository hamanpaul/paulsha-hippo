## ADDED Requirements

### Requirement: Canonical distiller configuration

The system SHALL use the Hippo configuration root as the single runtime authority for atomizer/distiller configuration. Legacy configuration MAY be read only by an explicit migration planner and MUST NOT be merged into normal runtime loading. Migration SHALL support dry-run, conflict detection, a hash-bound resolution file with per-field `canonical`, `legacy`, or explicit manual value choices and operator rationale, pre-apply backup, idempotent apply, and rollback. Incomplete, unresolved, stale-plan, or conflicting backend configuration SHALL fail closed before processing or requeue begins.

#### Scenario: Legacy and canonical config conflict
- **WHEN** migration finds different backend semantics in canonical and legacy configuration
- **THEN** dry-run SHALL report the conflict and apply SHALL refuse to continue until an explicit resolution is supplied

#### Scenario: Migration is idempotent
- **WHEN** a successfully migrated configuration is planned or applied a second time
- **THEN** the operation SHALL produce no semantic changes and runtime SHALL continue to load only the canonical source

#### Scenario: Explicit conflict resolution is hash-bound
- **WHEN** an operator resolves a canonical/legacy conflict in a reviewed resolution file
- **THEN** apply SHALL accept it only while all source hashes match the plan and SHALL persist the selected source/value and rationale in the migration manifest

### Requirement: Atomic deployed-surface attestation

A deployed Hippo instance SHALL expose version, build commit, and artifact identity for the package, copied hook scripts/hook environment, and systemd service. Upgrade SHALL run from an independent staged candidate runner that survives failure of the active package. Before the first mutation it SHALL fsync a write-ahead manifest, fence hook-triggered importers into durable spool-only mode, stop the timer and any active service, wait for writers to drain, and acquire the shared maintenance/dream locks. It SHALL then snapshot current state and a restorable old package/venv reference, install/switch the candidate artifact through a profile-specific adapter, migrate configuration, reinstall hooks and service, verify every surface, and only then re-enable writers/scheduling. A surface mismatch SHALL fail the upgrade/canary gate.

#### Scenario: Stale hook environment is detected
- **WHEN** the dream service package and copied hook environment resolve to different build identities
- **THEN** doctor/upgrade status SHALL report a deployment mismatch and MUST NOT authorize recovery cleanup or release acceptance

#### Scenario: Upgrade deploys one build everywhere
- **WHEN** upgrade apply completes successfully
- **THEN** package, hooks, and service SHALL attest the same release/build and service ExecStart SHALL resolve to the installed artifact interpreter

#### Scenario: Active writer prevents mutation
- **WHEN** the service or an importer cannot be stopped/fenced and drained before the manifest snapshot
- **THEN** upgrade SHALL perform no package/config/runtime mutation and SHALL leave the prior deployment active

### Requirement: Reversible no-data-loss upgrade and recovery

Before any config migration, historical retitle/reattribution, quarantine move, requeue, legacy rename, or cleanup, the system SHALL produce and fsync a timestamped manifest containing the operation plan, relevant file/ledger census, SHA-256 identities, a locally usable backup of the prior artifact/venv or equivalent package-manager restore input, and rollback commands runnable outside the mutable target environment. Append-only ledger prefixes MUST NOT be truncated or rewritten. Raw sessions and knowledge bodies MUST NOT be deleted; any move or rewrite SHALL have a deterministic source-to-target map and preserve slice ID/body/checksum semantics unless the manifest explicitly records a validated repair. Package rollback after new schema events exist SHALL be allowed only when forward-compatibility tests prove the old reader tolerates them; otherwise recovery SHALL use an isolated pre-upgrade snapshot or roll forward while preserving the post-upgrade delta.

#### Scenario: Recovery stops without data loss
- **WHEN** a bounded recovery batch fails midway
- **THEN** completed operations SHALL remain auditable, unprocessed inputs SHALL remain available, ledgers SHALL retain their prefixes, and rollback SHALL restore mutable deployment/config state without deleting newly valid notes

#### Scenario: Historical project cannot be proven
- **WHEN** a historical `_unknown` note has no source-session evidence for a project
- **THEN** recovery SHALL leave it `_unknown`, record it as unresolved, and MUST NOT guess a project

### Requirement: Complete backlog and health semantics

Machine-readable status SHALL report raw, split, retrying, parked, quarantined, and promoted session counts; oldest backlog age; notes created; generic-title, `_unknown`, invalid checksum/frontmatter counts; eligible/indexed coverage; backend/config/build identity; and a run correlation ID. Repeated malformed inbox artifacts SHALL enter a durable quarantine state with hash/reason/source evidence so subsequent dream cycles do not emit the same warning indefinitely. Health MUST distinguish process success from pipeline `ok`, degraded/partial, failed, and skipped outcomes.

#### Scenario: Split backlog is visible
- **WHEN** raw inbox is small but sessions remain in split/parked states
- **THEN** status SHALL report those states and SHALL NOT represent raw inbox depth as total backlog

#### Scenario: Malformed inbox is quarantined once
- **WHEN** an inbox artifact lacks required source metadata or cannot be parsed
- **THEN** it SHALL be preserved in quarantine with evidence and subsequent dream cycles SHALL not repeatedly warn about the same source artifact

### Requirement: NAME_MAX-safe atomic frontmatter update

Frontmatter updates SHALL use a same-directory temporary filename whose UTF-8 basename remains within the filesystem NAME_MAX even when the final filename itself uses the full allowed budget. The temporary name SHALL be unique enough for concurrent/crash-safe updates and SHALL be atomically replaced over the target. The implementation MUST NOT solve the problem by silently truncating slice IDs.

#### Scenario: Full-length valid target can be updated
- **WHEN** a knowledge filename is exactly the supported NAME_MAX byte length
- **THEN** frontmatter update and MOC linking SHALL complete without `ENAMETOOLONG`, preserve the complete slice ID, and leave no temporary file after success

### Requirement: Bounded and gated backlog recovery

Recovery SHALL require canonical config validation, deployed-surface attestation, and a service-effective backend probe before any requeue. It SHALL process deterministic repairs before LLM requeue, support dry-run and configurable small batches, and stop on backend failure, rising parked count, integrity regression, or resource threshold. Poisoned caches SHALL be preserved in failure evidence or quarantined according to their failure class rather than retried without bound. Legacy lock cleanup SHALL run only after the active hooks attest shard-lock support.

#### Scenario: Backend unavailable blocks requeue
- **WHEN** the service-effective backend probe fails
- **THEN** recovery apply SHALL perform no LLM requeue and SHALL leave all split/parked inputs intact

#### Scenario: Canary batch controls large backlog
- **WHEN** a stale deployment contains a large split backlog
- **THEN** recovery SHALL process only the configured canary batch, report per-state deltas and integrity results, and require explicit continuation for the next batch

### Requirement: Truthful CI and release identity

CI SHALL execute the repository test suite whenever matching test files exist, SHALL fail when project/test dependency installation fails, and SHALL expose a collected/executed test count greater than zero. Release identity SHALL be consistent across all version declarations and include build/artifact attestation. The Issue #34 release SHALL use PATCH version `0.1.1`; the final untagged candidate commit SHALL already contain that version, the finalized changelog, and a strict-valid active OpenSpec. All artifact, upgrade, rollback, and canary gates SHALL run against the exact candidate wheel hash; success SHALL add the final tag to the same commit without changing files or rebuilding a different wheel. Release evidence SHALL then be recorded and the change SHALL be closed with the official OpenSpec archive flow in a post-tag metadata commit that does not rebuild the artifact. No candidate tag outside the repository version grammar may be created.

#### Scenario: Test suite cannot be silently skipped
- **WHEN** the repository contains pytest files
- **THEN** CI SHALL install the project, collect and execute tests, and a skipped pytest step or zero collected tests SHALL fail the release gate

#### Scenario: Installed build is distinguishable
- **WHEN** an operator inspects a deployed package, hook environment, or service
- **THEN** the output SHALL identify `0.1.1`, build commit, and artifact identity consistently without relying only on the version string

### Requirement: Installed-service release acceptance

Release acceptance SHALL test a built wheel in clean-install and all supported upgrade profiles, including any declared package-target migration. The installed hooks and timer/service, not direct internal pipeline calls, SHALL process real supported-client sessions through import, atomization, knowledge publication, MOC/index, and recall. Each accepted atom SHALL preserve semantic content, use a specific title, carry the correct project and honest provenance, pass checksum/frontmatter validation, and be present in metadata and FTS indexes. Automatic consumption MAY be claimed only for a client with a real offered-to-Read trace; otherwise the release SHALL downgrade that capability while retaining producer/explicit-recall support. Applied SHALL be counted only from a real structured acknowledgement.

#### Scenario: Supported client completes the installed chain
- **WHEN** a real supported-client session is captured after wheel install or upgrade
- **THEN** evidence SHALL link hook event, import record, processing record, knowledge slice, index row, and recall result with the expected agent/session/project identities

#### Scenario: Unsupported client claim is downgraded
- **WHEN** a client cannot complete its documented installed ingress or consumption chain
- **THEN** the capability matrix and release notes SHALL downgrade that claim and release acceptance MUST NOT report the unproven capability as supported

### Requirement: Release canary, rollback, and issue closure

The release SHALL pass clean install, both supported upgrade profiles, rollback drill, and a canary/soak of at least three consecutive scheduled cycles, each with a unique new ingress session and at least one accepted atom. A skipped, zero-ingress, or zero-accepted-atom cycle MUST NOT count toward the soak. Canary SHALL show no new legacy locks, no unexpected generic-title or `_unknown` atoms, no parked/split growth, complete index coverage, and an `ok` pipeline status. Producer-correctness release MAY proceed with an explicit automatic-consumption capability downgrade, but Issue #34 MUST remain open until its nine-item traceability matrix points to committed implementation and attached release evidence, including a real offered-to-Read consumer trace.

#### Scenario: Canary regression blocks release
- **WHEN** any canary cycle produces an excluded note, deployment mismatch, growing failure state, or incomplete index coverage
- **THEN** final tag/publication SHALL be blocked and rollback or repair evidence SHALL be recorded

#### Scenario: Issue closes only with evidence
- **WHEN** all implementation tasks are merged and `v0.1.1` gates pass
- **THEN** the closing record SHALL include test run, artifact hash, migration/census manifest, rollback drill, canary ledger, and per-item Issue #34 evidence

#### Scenario: Producer release does not over-claim consumption
- **WHEN** all deterministic producer/release gates pass but no supported client completes an offered-to-Read trace
- **THEN** `v0.1.1` MAY publish with automatic consumption downgraded, but Issue #34 SHALL remain open and release notes MUST state the unproven capability
