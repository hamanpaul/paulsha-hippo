## MODIFIED Requirements

### Requirement: Complete backlog and health semantics

Machine-readable status SHALL report raw, split, retrying, parked, quarantined, and promoted session counts; oldest backlog age; notes created; generic-title, `_unknown`, invalid checksum/frontmatter counts; eligible/indexed coverage; backend/config/build identity; and a run correlation ID. Repeated malformed inbox artifacts SHALL enter a durable quarantine state with hash/reason/source evidence so subsequent dream cycles do not emit the same warning indefinitely. Health MUST distinguish process success from pipeline `ok`, degraded/partial, failed, and skipped outcomes.

Health counts SHALL be derived from knowledge slices only. Generated MOC index artifacts, identified by their `memory_layer: moc` frontmatter field rather than by filename convention, SHALL be excluded from the generic-title, `_unknown`, invalid checksum and invalid frontmatter counts. Counting generated artifacts that by design carry no slice frontmatter makes those counts unable to reach zero and destroys their diagnostic value.

#### Scenario: Split backlog is visible
- **WHEN** raw inbox is small but sessions remain in split/parked states
- **THEN** status SHALL report those states and SHALL NOT represent raw inbox depth as total backlog

#### Scenario: Malformed inbox is quarantined once
- **WHEN** an inbox artifact lacks required source metadata or cannot be parsed
- **THEN** it SHALL be preserved in quarantine with evidence and subsequent dream cycles SHALL not repeatedly warn about the same source artifact

#### Scenario: Generated MOC artifacts are not counted as broken slices
- **WHEN** the knowledge tree contains generated MOC index files carrying `memory_layer: moc`
- **THEN** health SHALL exclude them from the invalid frontmatter count, and a knowledge tree whose every slice is well-formed SHALL report an invalid frontmatter count of zero

#### Scenario: Genuinely broken slices are still counted
- **WHEN** the knowledge tree contains a slice that is missing required frontmatter fields and is not a generated MOC artifact
- **THEN** health SHALL count it in the invalid frontmatter count
