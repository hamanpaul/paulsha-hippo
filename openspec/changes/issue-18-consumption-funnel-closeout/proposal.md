---
status: accepted
work_item: issue-18-consumption-funnel-closeout
---

# Issue #18 consumption funnel closeout

## Requirements

- Preserve PR #59's session-level `offered → read → applied` implementation; do not
  redesign relevance, decay, or ablation in this change.
- Replace the capability matrix's unfinished Task 8 placeholder with the shipped
  `hippo usage funnel` operator contract.
- State session citation, unique-slice coverage, offered-to-read conversion, and
  `applied` separately. `applied` is an explicit acknowledgement and is never a
  read/citation proxy.
- Protect that documentation contract with a focused regression test.
- Close only after an exact candidate passes repository gates, an independent
  Google-domain adversarial review, a Luna Max maintainer review, exact-artifact
  installation, and production-memory read-only verification.

## Scope

This closeout may change the capability matrix, a focused documentation contract
test, the required changelog fragment, and workflow planning/report artifacts.
Relevance ranking, usage-based decay, and ablation remain owned by Issue #41.
