---
status: accepted
work_item: issue-34-atomization-release
---

# Tasks

## 0. Authority reset and verified baseline

- [x] 0.1 Audit `v0.1.1`: confirm the stale local tag pointed to `d04ba59`, confirm that commit is outside `main` ancestry and no remote tag/GitHub release uses it, then remove the local tag without force-moving it. Recheck all three surfaces before publication.
- [x] 0.2 Keep README/install guidance at release-candidate semantics while `v0.1.1` is absent; `v0.1.0` remains the only authoritative release.
- [x] 0.3 Preserve the merged CI-truth and checkout-shadowed wheel clean-install harness baseline from PR #35.
- [x] 0.4 Preserve the merged session/capture identity, complete ordered assistant content, minimum-32K fixed-budget zero-tool distillation, and canonical disposition/no-zero-slice baseline from PR #35.
- [x] 0.5 Preserve the merged hash-pinned `hippo recovery plan|apply|resume|rollback` baseline and the first-five importer recovery evidence; do not treat that canary batch as full production recovery.
- [x] 0.6 Treat Issue #36 timer/reconcile work as a completed non-blocking baseline, not an active Issue #34 release task.
- [ ] 0.7 Add the authoritative machine-readable readiness matrix with gate ID, state, evidence, rerun command, timestamp, candidate commit, and wheel SHA-256. Candidate drift must invalidate artifact-bound passes.
- [x] 0.8 Capture a fresh real Claude `offered → Read` baseline through installed hooks and append-only ledgers before implementation. Keep it explicitly pre-candidate; rerun the same gate against the pinned candidate before Issue #34/#39 closure.

## 1. Close remaining atomization contract gaps

- [ ] 1.1 Persist the LLM proposal title as canonical `title`; add a versioned bounded title-repair contract, shared generic-title gate, MOC fallback order, and write → MOC → index regression coverage.
- [ ] 1.2 Separate rich project-ID validation from a collision-resistant hashed filesystem directory key; inherit a known source project and consult the legacy/generated registry union only for `_unknown` inputs.
- [ ] 1.3 Add the end-to-end provenance contract across cache, processing ledger, atom frontmatter, round-trip readers, and tests: profile revision/tier/attempt, requested model/effort, observed model truth, verification state, command/config/skill/build identity, and fallback reason.
- [ ] 1.4 Preserve bounded sanitized stderr evidence for non-zero agent failures without recording prompts, outputs, secrets, tokens, or personal executable paths.
- [ ] 1.5 Finish per-session publication journal/commit-marker eligibility and recovery so partial files/edges cannot become MOC/index-visible; persist run ID and exact produced slice IDs for reconciliation.
- [ ] 1.6 Replace full-target-derived temporary names with NAME_MAX-safe same-directory atomic names; test exact byte limit, crash residue, concurrent attempts, and preservation of the full slice ID.
- [ ] 1.7 Run current and legacy Copilot history layouts through a real importer → inbox → atom fixture; a reader-only fixture or `empty-skip` is insufficient.
- [ ] 1.8 Add durable malformed-inbox quarantine plus complete raw/split/retrying/parked/quarantined/promoted counts, oldest age, integrity metrics, and run-level disk/frontmatter/metadata-index/FTS health reconciliation.

## 2. Canonicalize config and deploy one attested artifact

- [ ] 2.1 Make Hippo config the sole runtime distiller source; reject and migrate away legacy `openai-compatible`, direct HTTP/TCP, provider URL, API-key/env-name, OAuth, and secret-path fields. Any prohibited field with a non-empty value must block as `operator-redaction-required` without backup/copy/log/apply until sanitized outside Hippo.
- [ ] 2.2 Remove `HttpAgentClient` and provider-specific request/env wiring; route atomization, importer title generation, and SkillOpt only through one external CLI agent router. Child agents receive a fixed minimal non-secret env, never inherited `os.environ`; env-based auth requires an external launcher. Retire repo-owned Gemma provider proxy/launcher surfaces from the release package.
- [ ] 2.3 Add declarative profiles with typed traits/task classes, tier/priority, model, profile-specific effort allowlist/renderer, `shell=False` argv, stdin-only prompt, timeout, zero-tool eligibility, and fallback policy. Reject aliases/functions, `{PROMPT}`, shell interpolation, `--yolo`, `--autopilot`, permission bypass, and tool-enabled Dream profiles.
- [ ] 2.4 Implement deterministic Tier 1 (`claude`, `codex`) → Tier 2 (`agy`, `cg`) → Tier 3 (`co-gem`, `claude-gem`, custom local) routing with explicit same-tier priority, frozen-input whole-session restart, allowlisted error transitions, global deadline/attempt/call budgets, circuit breaker/cooldown, disabled CLI-native fallback, `degraded-success`, and single park on exhaustion.
- [ ] 2.5 Bind cache/provenance to task class, response-schema and router-contract versions, profile revision, tier, attempt, model, effort, command/config/skill/prompt hashes and fallback reason; never mix operations, schemas, chunks, or cache entries across profiles.
- [ ] 2.6 Add `hippo install all --force --dry-run|--force` as an ownership-manifest transaction: protected-state denylist, drift conflicts, sanitized Hippo-exclusive backups, writer/service fencing, shared-file hash plus owned-entry inverse patch/three-way rollback with no whole-file copy, external rollback runner, checked daemon reload/doctor/profile probes, and second-run idempotence.
- [ ] 2.7 Add version/build/artifact/config attestation to package, importer, CLI, hooks, service, doctor, and status; mismatch must fail closed.
- [ ] 2.8 Add an independent staged upgrade/rollback runner with write-ahead manifest, writer fencing/drain, restorable old artifact, profile-specific package switch, hook/service reinstall, and service-effective agent verification.
- [ ] 2.9 Reinstall project-registry producer wiring during managed upgrade and verify the atomizer consumes the generated registry contract.
- [ ] 2.10 Validate both `current-pipx` split-surface and `stale-system` large-backlog profiles, including old-reader forward compatibility or isolated-snapshot recovery.

## 3. Complete production recovery and disposition

- [ ] 3.1 Gate every recovery apply/resume on candidate surface attestation, canonical config validation, registry/source pins, and service-effective backend probe.
- [ ] 3.2 Execute deterministic repair/quarantine before LLM requeue; upgrade hooks before legacy-lock cleanup; expand only after a bounded batch passes stop conditions.
- [ ] 3.3 Run all remaining production recovery batches with no ledger truncation, raw/knowledge loss, guessed provenance/project, unbounded retry, or target-dependent rollback runner.
- [ ] 3.4 Produce a complete manifest for the audited 53-session high-risk cohort, assigning every session `recovered`, `retained`, `quarantined`, `parked`, or `manual-review` with evidence and no unexplained unknown.
- [ ] 3.5 Reconcile post-recovery disk/frontmatter/metadata-index/FTS state and attach before/after census, manifest hashes, stop-condition history, and no-data-loss proof to the readiness matrix.

## 4. Verify installed producer and consumer chains

- [ ] 4.1 From the installed candidate wheel, run hook → service → atom → MOC/index → recall for every claimed Claude/Codex/Copilot producer path; separately smoke every enabled distiller profile under the systemd environment and downgrade any path/profile that cannot complete safely.
- [ ] 4.2 Pass a synthetic semantic corpus: one reusable concept per atom, expected concept coverage, non-generic canonical title, correct project, honest provenance, valid checksum/frontmatter, and no unnecessary raw-transcript leakage.
- [ ] 4.3 Obtain a real shortlist offer followed by actual knowledge Read for each automatic-consumption claim. Missing Read may downgrade the capability and permit producer release, but must keep Issue #34 open; `applied` requires structured acknowledgement.
- [ ] 4.4 Prove Tier 1 failure advances in exact configured order, fallback success is `degraded-success` with prior attempts retained, profile cache entries do not cross-contaminate, safety/config errors do not fallback, and full chain exhaustion parks once within global budgets.

## 5. Freeze and test one release candidate

- [ ] 5.1 Complete implementation docs/changelog, set all version declarations to `0.1.1`, apply the existing `release:patch` label, complete the PR template, and strict-validate the active OpenSpec before freezing the untagged candidate commit.
- [ ] 5.2 Build one wheel from that exact commit and record commit plus wheel SHA-256 in the readiness matrix. No later artifact-bound evidence may use a different build.
- [ ] 5.3 Run full pytest, policy, OpenSpec validation, clean install, force install dry-run/apply/idempotence/rollback, both upgrade profiles, service-effective profile probes, fallback drills, and published-surface attestation against the pinned wheel.
- [ ] 5.4 If candidate commit or wheel hash changes, reset and rerun every affected artifact, upgrade, rollback, recovery, ingress, and canary gate.

## 6. Pass real scheduled soak and publish `0.1.1`

- [ ] 6.1 Reconfirm `v0.1.1` is absent locally, remotely, and from GitHub Releases before the first publication mutation; any mismatched pre-existing tag blocks release and must be audited/removed, never force-moved.
- [ ] 6.2 Pass three consecutive cycles genuinely triggered by the enabled systemd timer. Each cycle needs a unique new ingress, at least one accepted atom, `ok` pipeline status, complete index coverage, no excluded note, and no growing split/parked/quarantined state. Manual, direct-service, isolated, skipped, zero-ingress, or zero-accepted cycles do not count.
- [ ] 6.3 Require every hard readiness gate to be `passed` for the same candidate commit/wheel. A checkbox, merge, prose assertion, process exit 0, or waiver alone is not evidence.
- [ ] 6.4 Tag that exact tested commit as immutable `v0.1.1` without file changes or rebuild; publish the exact wheel/hash, create the GitHub release, update downstream pins, and run published-artifact smoke.

## 7. Close release metadata and Issue #34 honestly

- [ ] 7.1 Attach the readiness matrix, test/artifact hashes, deployed-surface attestation, migration/recovery census, 53-session disposition, rollback drill, and three scheduled-cycle ledgers to Issue #34.
- [ ] 7.2 Map all nine Issue #34 rows to committed implementation and evidence. If offered → Read is still absent, publish only with automatic consumption downgraded and leave the issue open.
- [ ] 7.3 After publication evidence is durable, run official `openspec archive issue-34-atomization-release` in a post-tag metadata-only commit that does not rebuild the release artifact.
- [ ] 7.4 Close Issue #34 only when producer, ingress, recovery, scheduled soak, publication, and real consumer Read evidence all satisfy the traceability matrix.
