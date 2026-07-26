## ADDED Requirements

### Requirement: session-level consumption funnel report

The system SHALL provide `hippo usage funnel --memory-root <path>` as a
read-only, session-level report over the offered and memory-usage ledgers. The
report MUST keep these meanings separate:

- session citation: whether a logical tool/session produced an attributable
  read after an offer;
- unique-slice coverage: how many distinct offered slices received an
  attributable read;
- offered-to-read conversion: the proportion of offered logical sessions with
  at least one later attributable read;
- applied: an explicit structured acknowledgement, not a read or citation.

The report SHALL expose JSON output and MUST NOT infer any one metric from
another. Live counts are runtime state and MUST NOT be pinned as source
requirements.

#### Scenario: applied is not counted as a citation

- **WHEN** a logical session has an `applied` event but no attributable read
- **THEN** the report SHALL count the explicit applied acknowledgement while
  leaving session citation and offered-to-read conversion unchanged

#### Scenario: source documentation does not pin mutable ledger totals

- **WHEN** operators run the report at different times over an append-only
  production ledger
- **THEN** the contract and capability matrix SHALL keep the metric definitions
  stable without requiring historical counts to remain constant
