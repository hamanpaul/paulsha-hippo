## ADDED Requirements

### Requirement: Versioned task memory payload envelope

Hippo SHALL provide a versioned task memory payload helper that builds and validates an envelope containing `schema_version`, `task_id`, bounded `intent`, `candidates`, `delivery`, `evidence`, and producer/adapter identity. Candidate ordering SHALL be deterministic, the payload SHALL contain at most three candidates, and denied candidates MUST NOT be exposed in the final envelope. Shareable summary/excerpt text SHALL be redacted before it leaves the helper.

#### Scenario: builder keeps the top three authorized candidates

- **WHEN** the caller passes four candidates with mixed authorization states and unordered ranks
- **THEN** the payload SHALL keep only the first three authorized candidates after deterministic sorting, and denied candidates SHALL be excluded

#### Scenario: validation preserves unknown optional fields

- **WHEN** a caller validates a payload that contains additional optional delivery metadata
- **THEN** validation SHALL keep those optional fields intact while still enforcing the required contract

### Requirement: Delivery outcome summary keeps read KPI strict

Hippo SHALL provide a delivery outcome summarizer for `inline`, `snapshot`, `note_fetch`, `ineligible`, and `failure`. `inline` and `snapshot` SHALL NOT count as read. `note_fetch` SHALL count as read only when the evidence contains `returned`, and SHALL count as applied only when `returned` and `applied` are both present. Failed events MAY expose a failure reason but MUST NOT be inferred as success.

#### Scenario: inline and snapshot stay out of read

- **WHEN** the summarizer receives `inline` or `snapshot` mode events without a `returned` event
- **THEN** the summary SHALL report `content_returned=false`, `counts_as_read=false`, and `counts_as_applied=false`

#### Scenario: note fetch requires returned before applied counts

- **WHEN** the summarizer receives `note_fetch` mode with `read_attempt`, `returned`, and `applied`
- **THEN** the summary SHALL report `content_returned=true`, `counts_as_read=true`, and `counts_as_applied=true`

#### Scenario: applied without returned does not count as read

- **WHEN** the summarizer receives `note_fetch` mode with only `applied`
- **THEN** the summary SHALL still report `content_returned=false`, `counts_as_read=false`, and `counts_as_applied=false`
