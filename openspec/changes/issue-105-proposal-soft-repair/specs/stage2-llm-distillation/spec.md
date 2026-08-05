## MODIFIED Requirements

### Requirement: Explicit canonical LLM disposition

The canonical response SHALL be exactly an object with fields `schema_version`, `disposition`, `reason`, and `findings`. `schema_version` SHALL equal `1`; `disposition` SHALL be either `findings` or `no_findings`; unknown fields on the canonical wrapper object and surrounding non-whitespace noise SHALL be invalid. `findings` SHALL contain one or more valid proposals and use `reason=null`. A proposal with a hard schema violation — missing or blank `title`, invalid `artifact_kind`, wrong field type, empty `body`, or empty `source_fragment_indices` — SHALL invalidate the entire response rather than publish a salvageable subset, because retrying a partially published response would non-deterministically lose findings. An unknown field on an individual proposal SHALL instead be a soft violation: the field SHALL be dropped deterministically, a warning naming the proposal index and the dropped field names SHALL be recorded for observability, the remaining fields of that proposal and every other proposal SHALL still be validated and produced, and the drop MUST NOT consume a retry or trigger fallback. When the source session has a known project, that pinned source project SHALL override model re-homing. `no_findings` SHALL contain an empty findings list and a non-empty reason. During one compatibility version, a non-empty legacy proposal array MAY be accepted. A legacy empty array, empty wrapper, empty stdout, malformed type, or unknown wrapper field SHALL be invalid and MUST NOT produce `promoted`.

`promoted` SHALL require `accepted_slices >= 1`. Only explicit successful `no_findings` responses from every chunk MAY terminate with zero slices, using the distinct terminal state `no-findings` and retaining the reasons.

Parked evidence SHALL retain only structured failure metadata plus the byte count and SHA-256 of invalid model stdout; it MUST NOT persist the stdout text because a backend can echo private prompt content. Each chunk attempt SHALL clear any previous chunk's in-memory stdout before execution.

#### Scenario: Empty legacy array is invalid
- **WHEN** the backend returns `[]` or a wrapper containing an empty findings array without an explicit `no_findings` disposition and reason
- **THEN** the attempt SHALL consume the bounded invalid-output retry path and MUST NOT record `promoted`

#### Scenario: Explicit no-findings terminates without a slice
- **WHEN** every chunk returns a valid `no_findings` response with a non-empty reason
- **THEN** the session SHALL enter terminal `no-findings`, archive its fragments, and never create a zero-slice promoted record

#### Scenario: Unknown proposal field is soft-repaired without killing the response
- **WHEN** a response contains valid proposals plus one proposal that carries a schema-unknown field such as `tags2`
- **THEN** the unknown field SHALL be dropped deterministically with a warning naming the proposal index and the dropped field names, and every proposal — including the repaired one — SHALL be produced without invalidating the response

#### Scenario: Hard proposal violation still invalidates the entire response
- **WHEN** any proposal in a response is missing `title`, has an invalid `artifact_kind`, an empty `body`, or an empty `source_fragment_indices`
- **THEN** the entire response SHALL be invalid, no proposal from that response SHALL be published, and the attempt SHALL follow the bounded retry/park path
