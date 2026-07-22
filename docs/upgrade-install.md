# Ownership-manifest install and upgrade boundary

The release package ships `paulsha_hippo/install-manifest.json`. It is the
only authority for files that `hippo install all` may create, update, retire,
or compensate. A target drift that is not explained by the previous Hippo
state blocks the transaction, even when `--force` is present.

```text
hippo install all --force --dry-run --target-root <config-root>
hippo install all --force --target-root <config-root>
```

The transaction writes an owned state file and a write-ahead journal below the
target. Exclusive files receive Hippo-only preimages. Shared JSON files never
receive a whole-file backup: only the declared owned entries and their inverse
patch are recorded. Rollback first checks that each owned key still has the
expected post-apply value; a concurrent operator edit returns
`rollback-blocked` and is preserved.

Protected paths include memory/raw/archive/knowledge, processing and dream
ledgers, retrieval indexes, recovery manifests, generated project registry,
shell startup files, external launcher/config roots, and credential stores.
Unknown stale-looking files are preserved.

The independent artifact runner is a staged, resumable transaction.  A plan
must include tokenized, allowlisted argv for every deployment phase and for
both rollback surface restores.  Missing phases are rejected before the
artifact is switched; a plan without these commands is safe for dry-run only.

The apply phase order is fixed and journaled:

```text
stop_drain
artifact_switch
hook_reinstall
service_reinstall
daemon_reload
service_restart
project_registry_producer_wiring
doctor
effective_profile_verification
```

The injected runner receives one phase at a time with a bounded timeout,
profile ID, transaction-root working directory, and a minimal environment.
It never receives inherited credentials, a shell command, or shell startup
files.  The default subprocess adapter uses `shell=False`; tests and a
release orchestrator can inject a runner to perform the environment-specific
fence, hook, service, and producer-wiring operations.

`project_registry_producer_wiring` is not permission to edit registry data.
Its successful result must attest both that producer wiring was installed and
that the atomizer consumed the generated registry contract.  The final
effective-profile result must attest the requested profile ID and candidate
artifact SHA-256.  Mismatches fail closed.

```text
hippo upgrade plan --candidate <wheel> --target-root <artifact-root> --out <plan.json>
hippo upgrade prepare --plan <plan.json> --transaction-root <tx>
hippo upgrade apply --manifest <tx>/upgrade.json --force
hippo upgrade rollback --manifest <tx>/upgrade.json
```

`plan` and `prepare` are non-deploying stages.  `apply --dry-run` validates the
prepared candidate and reports the fixed phase order without invoking a
runner or mutating the target.  A successful apply can be called again: the
second call returns `already-applied` without rerunning commands.  A failed
post-switch phase automatically restores the hash-pinned old artifact and
attempts `rollback_hook_restore` followed by `rollback_service_restore`.
The write-ahead manifest retains every phase attempt, command argv, bounded
result metadata, failure, rollback state, and artifact hash; a failed restore
is recorded as `rollback-blocked` rather than hidden.

The runner deliberately does not receive permission to touch memory/raw,
knowledge, append-only ledgers, indexes, project-registry data, credentials,
or shell rc files.  Those paths are outside the artifact transaction.
