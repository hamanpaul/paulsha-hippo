---
status: accepted
work_item: issue-18-consumption-funnel-closeout
---

# Issue #18 closeout design

## Design

The code path landed in PR #59 and remains unchanged unless a regression is
proven. The repository change closes the remaining documentation truth gap:
`docs/cross-cli-capability-matrix.md` must describe the installed
`hippo usage funnel` surface and its four non-interchangeable metrics without
historical “Task 8 fill” text.

A focused test reads the matrix as a durable contract. The test must fail on the
current placeholder, then pass only when the command and all four metric
meanings are present. It must not pin live counts, because ledger totals are
runtime state rather than source truth.

Delivery is bound to one exact candidate. Cortex uses
`codex/gpt-5.3-codex-spark` for RED/GREEN implementation and
`agy/gemini-3.6-flash-high` for foreign review. A direct
`codex/gpt-5.6-luna` review with `model_reasoning_effort=max` is then bound into
Cortex's exact-HEAD maintainer attestation. Any unresolved defect fails closed.

After merge, build and install from the exact merge commit, then run the
installed CLI against production memory in read-only mode. The issue comment
records commit, artifact hash, Cortex run/jobs, repository gates, doctor,
ownership, timer/oneshot status, hook diff, and the four separate measurements.
Only a real hook content change may produce a `needs_human` `/hooks` refresh.
