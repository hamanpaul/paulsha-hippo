---
status: accepted
work_item: issue-18-consumption-funnel-closeout
---

# Issue #18 consumption funnel closeout plan

## Tasks

1. RED: add a focused test that rejects the capability matrix's unfinished
   Task 8 placeholder and requires the installed `hippo usage funnel` command
   plus separate definitions for session citation, unique-slice coverage,
   offered-to-read conversion, and explicit `applied`.
2. GREEN: make the smallest documentation change that satisfies the contract.
   Do not change funnel code unless the test or verification proves a defect.
3. Preserve the stable metric meanings in the accepted
   `stage2-memory-usage-telemetry` OpenSpec delta. Add a `changelog.d/` fragment
   because the test is a governed code-path
   change. Do not bump `VERSION`.
4. Run the focused test, full pytest suite, `policy_check`, and the repository
   preflight command. Preserve machine-readable evidence.
5. Submit the exact candidate to `agy/gemini-3.6-flash-high`. Unhandled defects
   fail; explicitly bounded and documented residual risks do not fail by
   themselves unless the reviewer rebuts the impact analysis.
6. Submit the same exact candidate to `codex/gpt-5.6-luna` with
   `model_reasoning_effort=max`. Bind an approved, finding-free result to
   Cortex maintainer review evidence.
7. Merge through Cortex, build and install from the exact merge commit, then
   verify `hippo usage funnel --memory-root <memory-root> --json`, doctor,
   ownership, timer/oneshot state, and hook content.
8. Post commit/artifact/job/evidence identifiers to Issue #18 and close it.
   Start Issue #41 only after GitHub reports Issue #18 closed.
