## ADDED Requirements

### Requirement: Canonical distiller configuration

The system SHALL use the Hippo configuration root as the single runtime authority for atomizer/distiller configuration. Legacy configuration MAY be read only by an explicit migration planner and MUST NOT be merged into normal runtime loading. Runtime configuration SHALL describe external CLI profiles only and MUST NOT contain provider API-key values, credential env names, OAuth state, secret paths, provider base URLs, or direct HTTP/SDK backends. Migration SHALL support dry-run, conflict detection, explicit retirement of sanitized legacy direct-provider fields, a hash-bound resolution file with per-field `canonical`, `legacy`, `remove`, or explicit manual value choices and operator rationale, pre-apply backup of sanitized Hippo-exclusive inputs, idempotent apply, and rollback. Any prohibited direct-provider field with a non-empty value—including credential, credential env name, OAuth state, secret path, or provider URL—SHALL block as `operator-redaction-required`; Hippo SHALL identify only its field/path and MUST NOT copy, back up, apply, resolve, or log it. Migration MAY resume only after the operator sanitizes the source outside Hippo. Incomplete, unresolved, stale-plan, unsafe, or conflicting profile configuration SHALL fail closed before processing or requeue begins.

#### Scenario: Legacy and canonical config conflict
- **WHEN** migration finds different backend semantics in canonical and legacy configuration
- **THEN** dry-run SHALL report the conflict and apply SHALL refuse to continue until an explicit resolution is supplied

#### Scenario: Migration is idempotent
- **WHEN** a successfully migrated configuration is planned or applied a second time
- **THEN** the operation SHALL produce no semantic changes and runtime SHALL continue to load only the canonical source

#### Scenario: Explicit conflict resolution is hash-bound
- **WHEN** an operator resolves a canonical/legacy conflict in a reviewed resolution file
- **THEN** apply SHALL accept it only while all source hashes match the plan and SHALL persist the selected source/value and rationale in the migration manifest

#### Scenario: Credential-bearing legacy field blocks automatic migration
- **WHEN** migration encounters `api_key`, `api_key_env`, provider URL, OAuth, or secret-path fields
- **THEN** the plan SHALL identify only the field/path; any non-empty prohibited value SHALL block without backup or apply until the operator sanitizes it outside Hippo, after which the retired field MAY be removed from Hippo-owned config under a reviewed manifest

### Requirement: Manifest-driven forced release installation

The release install flow SHALL provide `hippo install all --force --dry-run` and `hippo install all --force`, with explicit service-enable behavior. Force cleanup SHALL use only positive ownership from a prior install manifest or a versioned legacy allowlist; absence from the new package alone SHALL NOT authorize deletion. Dry-run SHALL perform no mutation and SHALL classify every candidate as keep, update, remove, backup, or conflict. Apply SHALL fence and drain writers, stop/record timer and service state, acquire maintenance locks, write and fsync a mode-restricted backup/transaction manifest, use staged atomic replacement, verify `daemon-reload`, doctor, and service-effective profile probes, and restore prior enable/running state. For a shared config, the transaction SHALL store only the whole-file hash plus structured preimage/inverse patch for Hippo-owned entries and MUST NOT copy whole-file bytes. Every shared-file commit SHALL compare the current hash to the planned preimage, and rollback SHALL always use owned-entry three-way compensation that preserves concurrent non-Hippo changes or stop BLOCKED without mutation when unsafe. Whole-file backup/restore is permitted only for sanitized Hippo-exclusive files. Failure SHALL support rollback from a runner outside the mutable target. A second identical force install SHALL produce no semantic diff.

Force MAY clean only Hippo-owned retired config fields/files, managed hook entries/scripts/venvs, the dedicated Hippo Copilot hook file, current/legacy Hippo systemd units, and package-owned cache/temp. It MUST NOT modify or delete raw/archive/inbox/knowledge/memory data, append-only ledgers, indexes, recovery state, logs/locks, project registries, external agent launchers, shell startup files, OAuth/API-key stores, secret env files, unknown files, or non-Hippo entries in shared Claude/Codex settings. Targets SHALL reject symlinks, path traversal, broad root/home scopes, and ownership drift. A user-modified managed file SHALL become conflict + backup, not blind deletion.

#### Scenario: Dry-run identifies retired owned files without mutation
- **WHEN** an older release manifest owns a hook or config field that the candidate explicitly retires
- **THEN** `--force --dry-run` SHALL list the planned removal and backup while leaving filesystem bytes, service state, and config unchanged

#### Scenario: Unknown stale-looking file is preserved
- **WHEN** a file is absent from the candidate package but no prior manifest or legacy allowlist proves Hippo ownership
- **THEN** force install SHALL preserve it and report a conflict or unmanaged item rather than deleting it

#### Scenario: Force install rolls back atomically
- **WHEN** service installation, daemon reload, doctor, or an enabled-profile probe fails after mutation begins
- **THEN** rollback SHALL restore sanitized Hippo-exclusive preimages and prior service/timer state, while shared config SHALL be compensated only through Hippo-owned structured inverse patches and three-way merge; it SHALL preserve concurrent user changes, protected data, secrets, and append-only state

#### Scenario: Concurrent shared-config edit blocks destructive rollback
- **WHEN** a user changes a shared Claude/Codex config after force install commits but before rollback
- **THEN** rollback SHALL preserve that change through owned-entry compensation or stop BLOCKED and SHALL NOT overwrite the whole file with its preimage

#### Scenario: Force install is idempotent
- **WHEN** a completed force install is immediately repeated against unchanged state and artifact
- **THEN** the second plan SHALL contain no semantic update/removal and apply SHALL preserve all hashes and service state

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

### Requirement: Hash-pinned resumable importer recovery

The system SHALL expose `hippo recovery plan`, `apply`, `resume`, and `rollback`. Recovery sources SHALL be limited to frozen `archive/queue/**/*.json` payloads and transcript pointers whose content can be verified and pinned. Planning SHALL copy each verified transcript into the transaction root and reconstruct only from that hash-pinned snapshot, so a still-active external transcript MAY continue appending without invalidating or changing the plan. The transaction root identity SHALL include code, effective config, project registry, source-manifest pins, and batch size; a new candidate over the same source census MUST NOT overwrite or reuse an earlier plan's manifest, snapshots, or journal. A plan SHALL pin code, effective config, project registry, every selected source hash, and every transcript snapshot hash; identify each winner, old/new path and hash, decision, and expected ledger delta; and default to at most five selected sessions. Importer reconstruction and any later LLM replay SHALL be separate operations: recovered importer artifacts SHALL carry a machine-readable no-replay marker, and an already promoted session MUST NOT be blindly replayed.

Apply SHALL persist complete batch membership, a staging copy, preimage, and fsynced replace intent before using `os.replace` to commit an item. Apply and resume SHALL reject code/config/registry/source drift and target-path drift before mutation. Rollback SHALL compensate only paths committed by the selected recovery batch, restore byte-identical preimages or remove batch-created outputs, and SHALL NOT truncate or rewrite existing JSONL ledgers. A rolled-back item SHALL be eligible for a later apply. A recovered finding SHALL use a new slice ID. Automatic `supersedes` SHALL be added only when source, project, and canonical title match while body hash differs; ambiguous candidates SHALL remain side-by-side for human review.

#### Scenario: Interrupted apply resumes byte-equivalently
- **WHEN** recovery is interrupted at any journaled commit point and all pinned inputs remain unchanged
- **THEN** `resume` SHALL complete to the same bytes and manifest state as an uninterrupted apply

#### Scenario: Rollback only compensates this batch
- **WHEN** rollback follows a partially or fully applied batch
- **THEN** it SHALL restore only that batch's preimages, preserve unrelated/newer files, and leave all pre-existing JSONL bytes unchanged

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

### Requirement: Evidence-bound release readiness matrix

The release SHALL maintain one authoritative, machine-readable readiness matrix. Every gate SHALL contain a stable gate ID, state (`not-started`, `in-progress`, `passed`, or `blocked`), evidence reference, rerun command, execution timestamp, and—when artifact-bound—the exact candidate commit and wheel SHA-256. A checkbox, merged PR, successful process exit, or prose assertion alone MUST NOT produce `passed`. Changing the candidate commit or wheel hash SHALL invalidate every artifact-bound pass; source-only baseline evidence MAY remain passed only after applicability is revalidated. Hard release gates MUST NOT be waived into a passing state.

#### Scenario: Stale checkbox cannot pass a release gate
- **WHEN** a task is checked but lacks the required candidate pins or rerunnable evidence
- **THEN** its readiness state SHALL remain `not-started`, `in-progress`, or `blocked` and final publication SHALL remain blocked

#### Scenario: Candidate drift invalidates artifact evidence
- **WHEN** the candidate commit or wheel SHA-256 changes after an artifact, upgrade, rollback, recovery, or canary gate passed
- **THEN** every affected gate SHALL return to `not-started` until rerun against the new candidate

### Requirement: Fail-closed release tag authority

Before all hard release gates pass, `v0.1.1` SHALL NOT exist as a local tag, remote tag, or GitHub release. If a pre-existing tag points to a non-candidate commit, release SHALL be blocked and the tag SHALL be audited and removed rather than silently force-moved. The final tag SHALL point to the exact tested candidate commit, and the GitHub release plus published artifact SHALL be verified against the same wheel hash. Until publication, operator documentation SHALL retain release-candidate semantics and MUST NOT advertise an unavailable `@v0.1.1` install pin.

#### Scenario: Stale tag points outside candidate ancestry
- **WHEN** any local or remote `v0.1.1` tag resolves to a commit other than the tested candidate
- **THEN** publication SHALL be blocked, the mismatch SHALL be recorded, and the stale tag SHALL be removed before gates are rerun

#### Scenario: Final tag preserves tested identity
- **WHEN** all hard gates have passed for one candidate commit and wheel hash
- **THEN** `v0.1.1` SHALL be added to that exact commit without file changes or artifact rebuild, and published-artifact smoke SHALL verify the same hash

### Requirement: Installed-service release acceptance

Release acceptance SHALL test a built wheel in clean-install, force-reinstall, and all supported upgrade profiles, including any declared package-target migration. The installed hooks and timer/service, not direct internal pipeline calls, SHALL process real supported-client sessions through import, atomization, knowledge publication, MOC/index, and recall. Every enabled external-agent profile SHALL pass service-effective eligibility and a bounded isolated smoke; at least one test SHALL prove the declared fallback order, attempt provenance, cache separation, and `degraded-success`, while a separate exhaustion test SHALL park once. Each accepted atom SHALL preserve semantic content, use a specific title, carry the correct project and honest provenance, pass checksum/frontmatter validation, and be present in metadata and FTS indexes. Automatic consumption MAY be claimed only for a client with a real offered-to-Read trace; otherwise the release SHALL downgrade that capability while retaining producer/explicit-recall support. Applied SHALL be counted only from a real structured acknowledgement.

#### Scenario: Supported client completes the installed chain
- **WHEN** a real supported-client session is captured after wheel install or upgrade
- **THEN** evidence SHALL link hook event, import record, processing record, knowledge slice, index row, and recall result with the expected agent/session/project identities

#### Scenario: Unsupported client claim is downgraded
- **WHEN** a client cannot complete its documented installed ingress or consumption chain
- **THEN** the capability matrix and release notes SHALL downgrade that claim and release acceptance MUST NOT report the unproven capability as supported

### Requirement: Release canary, rollback, and issue closure

The release SHALL pass clean install, both supported upgrade profiles, rollback drill, complete production recovery, and a canary/soak of at least three consecutive systemd-timer-triggered scheduled cycles, each with a unique new ingress session and at least one accepted atom. Direct service invocation, manual pipeline execution, and isolated canaries MUST NOT count as scheduled cycles. A skipped, zero-ingress, or zero-accepted-atom cycle MUST NOT count toward the soak. Canary SHALL show no new legacy locks, no unexpected generic-title or `_unknown` atoms, no parked/split growth, complete index coverage, and an `ok` pipeline status.

The production recovery manifest SHALL enumerate every remaining batch and assign every member of the audited high-risk cohort (53 sessions at the PR #35 baseline) an evidence-backed `recovered`, `retained`, `quarantined`, `parked`, or `manual-review` disposition. It MUST NOT leave unexplained unknowns. Producer-correctness release MAY proceed with an explicit automatic-consumption capability downgrade, but Issue #34 MUST remain open until its nine-item traceability matrix points to committed implementation and attached release evidence, including a real offered-to-Read consumer trace.

#### Scenario: Canary regression blocks release
- **WHEN** any canary cycle produces an excluded note, deployment mismatch, growing failure state, or incomplete index coverage
- **THEN** final tag/publication SHALL be blocked and rollback or repair evidence SHALL be recorded

#### Scenario: Isolated or manual canary does not count toward soak
- **WHEN** a canary is started directly rather than by the enabled systemd timer
- **THEN** it MAY serve as diagnostic evidence but SHALL NOT increment the three-cycle scheduled soak count

#### Scenario: High-risk recovery cohort is fully dispositioned
- **WHEN** production recovery is proposed as complete
- **THEN** the manifest SHALL account for all 53 baseline high-risk sessions and all later selected batches without an unexplained member or data-loss delta

#### Scenario: Issue closes only with evidence
- **WHEN** all implementation tasks are merged and `v0.1.1` gates pass
- **THEN** the closing record SHALL include test run, artifact hash, migration/census manifest, rollback drill, canary ledger, and per-item Issue #34 evidence

#### Scenario: Producer release does not over-claim consumption
- **WHEN** all deterministic producer/release gates pass but no supported client completes an offered-to-Read trace
- **THEN** `v0.1.1` MAY publish with automatic consumption downgraded, but Issue #34 SHALL remain open and release notes MUST state the unproven capability
