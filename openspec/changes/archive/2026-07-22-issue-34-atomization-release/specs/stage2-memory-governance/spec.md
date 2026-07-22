## RENAMED Requirements

- FROM: `### Requirement: Janitor hygiene lint for untitled and raw-remote keys`
- TO: `### Requirement: Janitor hygiene lint for untitled titles`

## MODIFIED Requirements

### Requirement: Janitor hygiene lint for untitled titles

Janitor scan SHALL perform read-only hygiene lint for a knowledge record whose
frontmatter `title` equals `untitled`, emitting rule `title-untitled`. A
registry-valid remote-form project ID containing `/` is canonical rich metadata
and MUST NOT emit `raw-remote-key`; filesystem safety is enforced separately by
the collision-resistant project directory key. Lint MUST NOT modify files or
write lifecycle events. For machine-readable compatibility, `run_scan` SHALL
continue returning `lint` fields `untitled` and `raw_remote_key`, with
`raw_remote_key` equal to zero under this contract.

#### Scenario: Remote-form project remains clean

- **WHEN** a knowledge slice has a semantic title and project `github.com/hamanpaul/paulsha-hippo`
- **THEN** janitor SHALL emit no lint warning and `raw_remote_key` SHALL remain zero

#### Scenario: Untitled remote-form project reports only its title

- **WHEN** a knowledge slice has `title: untitled` and a registry-valid remote-form project
- **THEN** janitor SHALL emit exactly one `title-untitled` warning without modifying the slice or lifecycle ledger
