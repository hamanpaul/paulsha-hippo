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
verdict, attested in this worktree: `python3 -m pytest -q` reported `1634
passed, 4 skipped, 154 subtests passed, 2 failed`. The 2 failures are a known
environment false signal (`tests/test_project_resolver.py::ResolveAutoDetectTests`
detecting this worktree's own outer git repo instead of a clean non-repo
folder), not a candidate defect — see the gate's `evidence` field for the
full explanation. All other 15 gates (AR-02–AR-14, IC-01, IC-02) are
honestly `pending`: no live external CLI, build, publication, upgrade,
recovery, timer-soak, or consumer-hook evidence has been captured for this
candidate in this environment. Each gate's `evidence` field states what
0.1.2-specific evidence is still needed, and `rerun` records the exact
command to (re)produce it.

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
