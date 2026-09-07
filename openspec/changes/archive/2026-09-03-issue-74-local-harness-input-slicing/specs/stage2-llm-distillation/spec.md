## ADDED Requirements

### Requirement: Local-harness pass-2 input slicing by fragment indices

The contrib local-vllm harness's second map-reduce pass (per-concept write) SHALL slice the rendered prompt down to the fragment blocks named by the concept's `fragment_indices`, expanded by a ±1 neighbor window, and SHALL preserve the prompt preamble and the `## Output` instruction block verbatim, instead of resending the full fragment payload for every concept. Slicing SHALL be a pure string transformation over the existing `[fragment N]` markers and MUST NOT require any change to the prompt contract produced by the atomizer prompt builder.

When slicing cannot be trusted — no fragment markers are found, the index set is empty, or every index is out of range — the harness SHALL fall back to the unmodified full prompt and SHALL emit a warning; a slicing failure MUST NOT cause the concept write itself to fail.

#### Scenario: Concept write sends only its fragment neighborhood
- **WHEN** pass 2 writes a concept whose `fragment_indices` cover a strict subset of the session's fragments
- **THEN** the request payload SHALL contain only the selected fragment blocks (±1 neighbor) plus the preamble and the `## Output` block, and SHALL be smaller than the full prompt

#### Scenario: Untrusted slicing falls back to the full prompt
- **WHEN** slicing finds no `[fragment N]` markers, or the concept's index set is empty or entirely out of range
- **THEN** the harness SHALL send the unmodified full prompt, SHALL log a warning, and the concept write SHALL proceed without failing
