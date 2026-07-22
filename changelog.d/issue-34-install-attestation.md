### Added

- Ownership-manifest `hippo install all --force --dry-run|--force` with protected
  denylist, idempotent transaction backup/rollback, shared-entry compensation,
  and independent hash-bound artifact upgrade/rollback commands.
- Installed-wheel harness and an explicit pending release-readiness matrix for
  candidate, service, recovery, soak, and consumer evidence.
- Install transaction state is written under the target surface, never beside
  a read-only wheel manifest; launcher, service, and credential paths are
  denylisted.
- Real install/upgrade apply now requires reviewed tokenized runtime/command
  plans; missing fencing, compensation, package-switch, or attestation phases
  fail before mutation. Dirty-checkout wheels are explicitly attested as dirty
  and rejected by doctor.
