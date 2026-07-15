## 1. Restore test and release truth

- [ ] 1.1 Replace the GitHub Actions test-suite glob detection with a robust check, remove install error swallowing, and add a workflow contract test proving pytest is collected and executed.
- [ ] 1.2 Add build/version attestation across package, importer, CLI, hooks, service, and artifact metadata; defer the version bump only until the final candidate-freeze task, before any artifact/upgrade/canary gate.
- [ ] 1.3 Add wheel clean-install and installed-surface test harnesses that do not import from the checkout by cwd shadowing.
- [ ] 1.4 Add the policy-required PR template and arrange the authorized `release:0.1.1` label before the version-freeze PR can merge.

## 2. Preserve session content and atom semantics

- [ ] 2.1 Add a dedicated session-title field; update adapters/title cache/frontmatter rendering so original assistant summary survives unchanged.
- [ ] 2.2 Persist LLM proposal title as canonical `title`, define a versioned title-repair cache/attempt contract, update MOC fallback order, enforce the shared generic-title gate before promotion, and add full write → MOC → index regression coverage.
- [ ] 2.3 Define rich project-ID validation separately from a collision-resistant hashed filesystem directory mapping; inherit known source project and union-read legacy/generated registries only for unknown sources.
- [ ] 2.4 Introduce an agent-result/provenance contract and integrate it through cache, processing ledger, note frontmatter serialization/round-trip, and E2E assertions with requested-versus-observed model truth and sanitized backend/config/skill/build fingerprints.
- [ ] 2.5 Preserve a bounded sanitized stderr excerpt for non-zero agent failures without logging prompt/output/secrets.
- [ ] 2.6 Add per-session publication journal/commit-marker recovery so a mid-write or mid-edge failure cannot expose partial atoms or duplicate relations.

## 3. Canonicalize configuration and deployed surfaces

- [ ] 3.1 Make Hippo config the only runtime distiller source; implement hash-bound per-field conflict resolution plus legacy migration dry-run/idempotence/backup/rollback tests.
- [ ] 3.2 Add an independent staged upgrade runner and write-ahead plan/prepare/apply/rollback flow covering writer fencing/drain, restorable old artifact, profile-specific package switch, hook environment/scripts, service unit, and service-effective backend validation.
- [ ] 3.3 Add doctor/status output for per-surface version/build/artifact/config attestation and fail closed on mismatch.
- [ ] 3.4 Reinstall project-registry producer wiring during managed upgrade and ensure atomizer consumes the generated registry contract.

## 4. Close persistence, ingress, and health gaps

- [ ] 4.1 Replace full-target-derived MOC temporary names with NAME_MAX-safe same-directory atomic names; test exact byte limit, crash residue, and concurrent attempts.
- [ ] 4.2 Support current and legacy Copilot session layouts, with a real layout fixture through importer → inbox → atom rather than `empty-skip`.
- [ ] 4.3 Add durable malformed-inbox quarantine and full state/age backlog metrics; prevent repeated warning-only loops.
- [ ] 4.4 Pass a run ID into atomization, return/persist exact produced slice IDs, and add run-level disk/frontmatter/metadata-index/FTS reconciliation plus health state that cannot be inferred solely from process exit code.

## 5. Build reversible migration and recovery

- [ ] 5.1 Implement independent census/SHA-256 manifest plus dry-run/apply/rollback for config, legacy filename, retitle, project reattribution, quarantine, index rebuild, and lock cleanup operations.
- [ ] 5.2 Gate recovery on deployed-surface attestation and service-effective backend probe; process deterministic repairs before bounded LLM requeue.
- [ ] 5.3 Add current-pipx split-surface and stale-system large-backlog upgrade fixtures; prove no ledger truncation, raw/knowledge loss, unbounded retries, guessed provenance/project, or rollback-runner dependency on the replaced target; test old-reader forward compatibility or force isolated-snapshot recovery.
- [ ] 5.4 Document maintenance ordering: upgrade hooks before legacy lock cleanup; repair/quarantine before requeue; canary batch before expansion.

## 6. Verify installed ingress and consumption

- [ ] 6.1 Run installed hook → service → atom → MOC/index → recall E2E for each claimed supported client; downgrade unsupported claims.
- [ ] 6.2 Verify a real shortlist offer followed by actual knowledge Read for automatic-consumption claims; otherwise downgrade that capability and keep Issue #34 open while allowing a producer-correctness release; record applied only with a real structured acknowledgement.
- [ ] 6.3 Add synthetic semantic corpus acceptance: one concept per note, expected concept coverage, non-generic title, correct project, no unnecessary raw transcript leakage, honest provenance.

## 7. Release `0.1.1`

- [ ] 7.1 Complete implementation docs, create `[0.1.1] - <release-date>`, reset `[Unreleased]`, update every version declaration to `0.1.1`, complete implementation tasks, and strict-validate the active change before freezing the untagged final candidate commit.
- [ ] 7.2 Build one commit/hash-addressed candidate wheel from that exact commit and execute full pytest/policy/spec validation, clean install, both upgrade profiles, recovery canary, no-data-loss census, rollback drill, and three consecutive scheduled cycles each containing unique ingress and an accepted atom.
- [ ] 7.3 After all artifact gates pass, tag/publish the exact tested commit and wheel as immutable `v0.1.1` without changing files or rebuilding; then update downstream pins and run published-artifact smoke.
- [ ] 7.4 Attach test/artifact/migration/rollback/canary evidence to Issue #34 and map all nine items; mark release tasks complete and run official `openspec archive issue-34-atomization-release` in a post-tag docs/spec closeout. Close the issue only after a real offered-to-Read trace; otherwise publish with the capability downgraded and leave the issue open.
