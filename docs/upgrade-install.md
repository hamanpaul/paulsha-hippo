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

The independent artifact runner is deliberately narrower:

```text
hippo upgrade plan --candidate <wheel> --target-root <artifact-root> --out <plan.json>
hippo upgrade prepare --plan <plan.json> --transaction-root <tx>
hippo upgrade apply --manifest <tx>/upgrade.json --force
hippo upgrade rollback --manifest <tx>/upgrade.json
```

It does not stop services, drain writers, reinstall hooks, mutate project
registry, or perform live profile probes. Those are explicit main-agent gates;
the runner reports service verification as pending and provides the artifact
hash/fence/rollback evidence needed for the next phase.
