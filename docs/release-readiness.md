# Release candidate readiness

The authoritative machine-readable gate list is
[`reports/verify/release-readiness-matrix.json`](../reports/verify/release-readiness-matrix.json).
A fresh candidate always starts with every gate `pending`, with no candidate
commit or wheel hash, until the main agent runs the artifact-bound and live
checks. See below for the currently bound candidate; the 0.1.1 section further
down is kept as historical record of that release's readiness process.

## 0.1.2 release candidate readiness

The matrix is currently rebound to the 0.1.2 candidate at commit
`c147218353bdc1e06f6a2f1cbc59a3a61130ceb6` (main HEAD). `wheel_sha256` is
`null` because the 0.1.2 wheel has not been built yet.

Of the 16 gates, only **AR-01** (full test suite) carries a real `passed`
verdict, attested from two independent clean environments: the candidate
commit's own GitHub Actions `Tests` run (conclusion `success`), and a local
non-nested sibling worktree where `python3 -m pytest -q` reported `1636
passed, 4 skipped, 154 subtests passed, 0 failed`.

Note on measurement environment: running the suite from a worktree nested
*under* the repo directory (`.claude/worktrees/*`) produces 2 failures in
`tests/test_project_resolver.py::ResolveAutoDetectTests`, because
`resolve_project()` detects the outer repo's git toplevel instead of a clean
non-repo folder. That is a false signal from the measurement environment, not
a candidate defect — attestation must therefore always be taken from a
non-nested checkout. All other 15 gates (AR-02–AR-14, IC-01, IC-02) are
honestly `pending`: no live external CLI, build, publication, upgrade,
recovery, timer-soak, or consumer-hook evidence has been captured for this
candidate in this environment. Each gate's `evidence` field states what
0.1.2-specific evidence is still needed, and `rerun` records the exact
command to (re)produce it.

**AR-11 note (2026-07-28) — 13 clean timer cycles that do *not* count.**
From `2026-07-27T13:00:42Z` to `2026-07-28T01:00:42Z` the dream service ran 13
consecutive systemd-timer cycles: hourly with no gaps, zero
`system busy`/`low memory`/lock skips, every run `status: ok` with `errors: 0`
and zero warnings across all three passes, all on `build_commit c147218…`, and
`index_coverage.eligible == indexed == 933` throughout.

**None of those 13 cycles count toward the AR-11 soak.** The canonical
requirement (`openspec/specs/atomization-release-integrity/spec.md`, "Release
canary, rollback, and issue closure") is *"at least three consecutive
systemd-timer-triggered scheduled cycles, each with a unique new ingress
session and at least one accepted atom"*, and states explicitly that *"a
skipped, zero-ingress, or zero-accepted-atom cycle MUST NOT count toward the
soak"* and that *"direct service invocation, manual pipeline execution, and
isolated canaries MUST NOT count as scheduled cycles."* All 13 cycles had
`health.eligible = 0`, `atomize.slices = 0`, `atomize.produced_slice_ids = []`
and `atomize.split_sessions = 0` — every one is a zero-ingress,
zero-accepted-atom cycle. AR-11 therefore stands at **0/3**, and remains
`pending`.

This matters as a worked example: the 13 cycles are genuinely healthy and every
number above was independently verified against the raw ledger. The error to
avoid is not fabricated data — it is drawing a pass verdict from real data that
does not meet the actual pass condition.

This rebind exists specifically to avoid repeating the AR-11 mistake from
0.1.1: that gate's evidence once read "three consecutive systemd timer runs
... were ok," but the dream ledger later showed all three runs were actually
`partial`. No gate in this matrix may be marked `passed` without an agent
having actually executed the check and read its output in this run of events.

## 0.1.1 release candidate readiness

This repository contains the implementation side of Issue #34/#39. The
authoritative machine-readable gate list is
[`reports/verify/release-readiness-matrix.json`](../reports/verify/release-readiness-matrix.json).
It intentionally starts with every gate `pending`, with no candidate commit or
wheel hash, until the main agent runs the artifact-bound and live checks.

## Implemented local contract

- Distillation uses the canonical external headless profile router. Hippo does
  not own HTTP/TCP provider clients, API keys, OAuth, provider URLs, or secret
  stores.
- `hippo install all --force --dry-run` plans only manifest-owned changes;
  real apply additionally requires a reviewed `--runtime-plan` covering writer/
  service fencing, compensation, doctor, and enabled-profile probes. Protected
  memory, ledger, index, recovery, project-registry, shell-rc, launcher, and
  credential paths are rejected. Shared JSON uses owned-entry compensation.
- `hippo upgrade plan|prepare|apply|rollback` stages one hash-bound artifact
  with a write-ahead manifest and writer fence. Real apply requires a complete
  reviewed `--command-plan`, including the pipx package switch, rollback switch,
  registry producer/consumer attestation, doctor, and effective profile/hash.
- Publication journals keep targets and relation edges invisible until a
  matching commit marker. Incomplete journals are recovered before the next
  atomization pass.
- Atomization treats a valid non-`_unknown` source project as authoritative even
  when the generated registry is stale. Every proposal must also retain a
  deterministic textual anchor to its declared source fragments; prompt/output
  contract leakage rejects the complete response before publication. The
  packaged atomization skill and runtime prompt use the same canonical response
  object contract.
- `hippo recovery plan --source-manifest <prior-manifest>` 沿用先前已審查的
  frozen source set，但以目前安裝候選版重新產生 code/config/registry pins 與
  planned artifacts；live archive 後續新增的 active session 不會擴張既定
  recovery scope，authority manifest 自身漂移則 fail closed。

## Evidence boundary

The following are not claimed by local tests: installed systemd hook/service
chains, service-effective profile probes, live fallback against real CLIs,
production recovery, the 53-session disposition, three timer-triggered soak
cycles, consumer `offered → Read`, release tag, wheel publication, or Issue
#34 closure. The main agent must attach rerunnable evidence for those gates to
the readiness matrix using the exact candidate commit and wheel SHA-256.

The timer/load gate remains unchanged: this implementation does not alter the
hourly timer or its load/memory eligibility behavior.
