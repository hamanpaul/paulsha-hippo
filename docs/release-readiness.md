# Release candidate readiness

The authoritative machine-readable gate list is
[`reports/verify/release-readiness-matrix.json`](../reports/verify/release-readiness-matrix.json).
A fresh candidate always starts with every gate `pending`, with no candidate
commit or wheel hash, until the main agent runs the artifact-bound and live
checks. See below for the currently bound candidate; the 0.1.1 section further
down is kept as historical record of that release's readiness process.

## 0.1.2 release candidate readiness

**Current state (2026-08-07) — release freeze in effect; matrix binding is
knowingly stale; AR-11 soak restarted at 0/3.** Everything below this block is
history, kept for the rebind/attestation trail. Read this block first: several
statements further down (notably "`wheel_sha256` is `null`" and the 13-of-16
attestation) were true when written and are no longer.

*Freeze.* `main` is frozen at `4d2b1f230a0160d6fe9d9d453bd47235ec9b5f93`
(#121). No further `code_paths` changes land until 0.1.2 ships; subsequent
fixes go to 0.1.3. Open at freeze time and deferred: #96, #117, #118 (0.1.3),
plus #99 which stays open for the outstanding copilot large-payload root-cause
measurement (its fail-fast half shipped in #112).

*The matrix binding is stale, deliberately.*
`reports/verify/release-readiness-matrix.json` still binds
`f5df39496de17c8b4fb3aa88dfa698b7c547aaf4` with `wheel_sha256`
`b942feb2296a8eff2d161fab76e82c0be8ad05eac1ec8a7510f14d3756f1f1a4`. **Fourteen
commits have landed since** (#95, #97, #100, #103, #104, #108, #111, #112,
#113, #114, #115, #116, #110, #121). Under `readiness.bind_candidate()`'s drift
rule, every `passed` gate in that file — including the 13-of-16 attestation
recorded below — is therefore **zombie evidence attesting a code state that is
no longer the candidate**, and the wheel hash is the wheel of a superseded
commit. The rebind is deliberately deferred rather than done now: rebinding
drops all evidence and forces a full re-attestation pass, which is only worth
paying once, after AR-11 soak completes on the deployed build. Until then this
paragraph — not the `passed` flags in the JSON — is the accurate statement of
gate status.

*Deployment.* `4d2b1f2` was deployed on 2026-08-07 and verified:
`HIPPO_BUILD_COMMIT` matches repo HEAD, `hippo doctor` reports no FAIL/WARN,
and the deployed copy (not the repo checkout) was read back directly for
`FIXED_TIMEOUT_SECONDS=600`, `FIXED_SESSION_DEADLINE_SECONDS=1200` and cg
`eligible(chunk_count=7) -> (False, "session_size")`. A complete deployment has
**four independent sync points**, only the first of which `pipx install
--force` covers: (1) the package itself; (2) the live config
`~/.config/paulsha-hippo/config.yaml` — `external_agents.deadline_seconds` must
equal `FIXED_SESSION_DEADLINE_SECONDS` exactly or config load fails closed on
*every* cycle, and cg's `max_session_chunks` from #112 is not refreshed from the
template because user values win; (3) the local-vllm harness at
`~/.local/bin/local-vllm`, a symlink to a standalone copy of
`contrib/local-harness/harness.py` that ships outside the wheel — it still
carried the pre-#110 version until this deployment; (4) `hippo install hooks`,
because hook scripts also carry the build commit and are not updated by `pipx
install` (missing this surfaces only as `hippo doctor`'s
`FAIL hooks build mismatch`).

*AR-11 soak restarted.* The build change opened a new window at
`2026-08-07T05:00:05Z`; that first cycle was `ok` with static regression
markers and zero ingress, i.e. neutral. **Soak is 0/3 for this build.** The
3/3 that build `1299fa1` completed in the `2026-08-04T03:00Z` →
`2026-08-06T01:00Z` window is **not transferable** — same rule that voided the
`76edb93` attestation. `~/.local/share/hippo-ops/soak-check.py` reproduces the
window analysis from the append-only ledger at any time.

*Remaining work, in order.* (1) accumulate 3 qualifying cycles on `4d2b1f2`;
(2) re-run AR-05 — the codex quota that blocked it reset on 2026-08-05, so it
is now runnable; (3) rebind the matrix to the shipping candidate and re-run the
artifact-bound gates (AR-01 test counts have moved, AR-02/AR-03 need a wheel
rebuilt from that commit); (4) AR-14 GitHub Release and assets. Note that the
`VERSION` bump commit itself changes the candidate again — if that commit is
deployed, the soak window reopens, so either bump without redeploying or budget
for a fresh soak.

*Watch item.* `02:00Z` has been a recurring `partial` slot (2026-07-31, 08-03,
08-04, 08-06 — each one `parked` +1, every time the whole fallback chain
exhausted). #110, #112 and #121 all target that failure shape and are now
deployed for the first time; whether `02:00Z` stops parking is the first real
signal that they worked. Tracked as #119, whose remaining scope is narrowed to
the tier-1 per-call timeout sub-shape.

---

**History below.** The matrix was rebound to the 0.1.2 candidate at commit
`f5df39496de17c8b4fb3aa88dfa698b7c547aaf4` (main HEAD, chosen by the
maintainer on 2026-07-30). *As of that rebind* `wheel_sha256` was `null`
because the 0.1.2 wheel had not been built yet; the 2026-07-31 attestation pass
below then built it. See the current-state block above for what supersedes
this.

**Rebind history.** The candidate was previously
`2f3dc981e6c283fbe3f85ccc9fbd7cb79c8ae809`; eight merges landed after it
(#85 chunk-scaled chain budget, #88 `window_older` STAT_KEYS, #90 chunk
retention/provenance, #91 AR-11 soak window semantics, #92 idle-gate
`max-load` 4.0, #93 shortlist offer early-stop, #94 importer trivial-session
gate, plus the #87 issue-close bookkeeping), so that binding — and the AR-01
evidence attached to it — went stale. Before that, the candidate was
`c147218353bdc1e06f6a2f1cbc59a3a61130ceb6`; three merges landed after it
(#75 session budget, #76 policy v1.0.15, #78 tier-1 plan-mode contract fix),
so that binding — and the AR-01 evidence attached to it — went stale.
`readiness.bind_candidate()` encodes exactly this rule: on candidate drift,
every `passed` gate reverts to `pending` and its evidence is dropped. AR-01 was
therefore re-run from scratch against the new candidate rather than carried
over. (The helper itself requires a non-empty `wheel_sha256` and cannot be
called while the wheel is unbuilt, so the rebind is applied by hand under the
same semantics, then re-validated through `readiness.load_matrix()`.)

Of the 16 gates, only **AR-01** (full test suite) carries a real `passed`
verdict, attested from two independent clean environments that agree exactly:
the candidate commit's own GitHub Actions `Tests` run (conclusion `success`,
`1700 passed, 4 skipped, 158 subtests passed in 105.69s`), and a local
non-nested sibling worktree where `python3 -m pytest -q` reported `1700
passed, 4 skipped, 158 subtests passed in 125.31s, 0 failed`. (The growth
from the previous binding's 1646 tests is exactly the test surface added by
this batch of merges.)

Note on measurement environment: running the suite from a worktree nested
*under* the repo directory (`.claude/worktrees/*`) produces 2 failures in
`tests/test_project_resolver.py::ResolveAutoDetectTests`, because
`resolve_project()` detects the outer repo's git toplevel instead of a clean
non-repo folder. That is a false signal from the measurement environment, not
a candidate defect — attestation must therefore always be taken from a
non-nested checkout. All other 15 gates (AR-02–AR-14, IC-01, IC-02) are
honestly `pending`: no build, publication, upgrade, recovery, timer-soak,
external-profile-probe, or consumer-hook evidence has been captured for this
candidate. Each gate's `evidence` field states what 0.1.2-specific evidence is
still needed, and `rerun` records the exact command to (re)produce it.

One environment note has changed since the previous rebind: AR-05 used to
record that this machine had no probeable external CLI. It now does — `claude`
2.1.220, `codex` 0.145.0 and `cg` are all on `PATH` and were exercised on
2026-07-28 — so AR-05 is runnable; it is `pending` only because no probe output
has been captured for *this* candidate. Most of the remaining gates are gated
on one missing artifact rather than on defects: AR-02/AR-03/AR-07/AR-14 all
wait on a built wheel (`wheel_sha256` is still `null`), and AR-05/AR-06/AR-08/
IC-01 wait on that wheel being installed into a clean environment.

**Gate attestation run (2026-07-31) — 13 of 16 gates passed for candidate
`f5df394`.** A full local gate-execution pass (build, clean-venv install,
config migration, live doctor probes, natural degraded-path corpus evidence,
publication/rollback test families, three-client hook ledger evidence,
staged-upgrade fail-closed drill on isolated targets, recovery CLI full-cycle
drill on an isolated mini-root plus a read-only independent index census of the
live root, policy/openspec/diff validation, and a batch closeout report at
`reports/verify/release-0.1.2-closeout.md`) moved AR-02/03/04/06/07/08/09/10/
12/13 and IC-01/02 to `passed` — each entry in
`reports/verify/release-readiness-matrix.json` carries the verbatim numbers,
hashes and honest caveats. The three remaining gates: **AR-05** stays pending
solely because the codex tier-1 probe is blocked by the provider usage limit
(resets 2026-08-05; claude, cg and local-vllm all probed green); **AR-11** soak
stands at 1/3 in a healthy window (the qualifying `19:00:42Z` cycle also
contains the AR-06 degraded-path publications); **AR-14** is deferred by
maintainer decision. Known follow-ups recorded, none release-blocking: the
`hippo search` CLI errors with `no such column: build` against the current
retrieval index; `build_release_artifact.py`'s version field is env-driven and
decoupled from `pyproject.toml`; three OpenSpec changes (issue-41/64/80) remain
unarchived with unmaintained task checklists; and the version-bump commit at
release time will change the candidate, requiring a wheel rebuild, matrix
rebind, and (if redeployed) a fresh soak window.

**AR-11 note (2026-07-30, this rebind) — window semantics codified; 3/3
achieved on the *previous* deployed build; count restarts for this candidate.**
PR #91 (`ed1ea37`, in this candidate) codified the soak counting semantics the
earlier notes below had to leave open: soak counting uses *window semantics* —
a neutral cycle (idle-gate skip, absent timer slot, zero-ingress or
zero-accepted-atom executed cycle with `ok` status and no regression-marker
growth) neither increments nor resets the count; a non-`ok` executed cycle,
regression-marker growth relative to the previous executed cycle (including
invalid-checksum backlog), or **a change of the deployed candidate build**
closes the window and resets the count; a window expires after seven calendar
days without a new qualifying cycle. Under those semantics the deployed build
`76edb93…` completed a full **3/3 soak** on 2026-07-30 (window opened
`03:00:32Z` by the #90 deployment build-change; qualifying cycles `03:00:32Z`
split=3/slices=47, `10:00:42Z` 1/5, `11:00:42Z` 2/9; nine executed cycles all
`ok`; parked=9, generic_title=1, unknown_project=6, invalid_checksum=18 static
throughout; the `02:00Z` idle skip is neutral; no manual runs). That evidence
lives in the append-only dream ledger and stands for `76edb93…` — **it does
not transfer to this candidate**: `f5df394…` was deployed at
`2026-07-30T11:24Z`, which under the same build-change rule opened a fresh
window (first cycle `12:00:42Z`, `ok`, zero-ingress, neutral). AR-11 for this
candidate therefore remains `pending` at 0/3, with the path to 3/3 now
demonstrated rather than conjectured.

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

**AR-11 update (2026-07-28, this rebind) — the blocker moved, the count did
not.** The reason no cycle could produce an accepted atom was that the tier-1
`claude` profile carried `--permission-mode plan`, so a real atomization
payload came back as prose asking how to proceed rather than a single JSON
document (issue #77, fixed in PR #78 — i.e. in this very candidate). The
`05:00:42Z` timer cycle parked session `claude-code:7d35853a…` with
`failure_kind: invalid_output`, 606 bytes, 86.4s; re-running that same
session's chunk 0 after the fix, through the deployed runtime and deployed
config, returned exit 0, 190.5s, 15,858 bytes of valid schema-1 JSON accepted
by `llm_output.parse_response()`.

**AR-11 nonetheless stays 0/3.** No qualifying timer cycle has occurred since
the fix, and one structural obstacle remains: a single chunk costs ~190–244s
while a 47-fragment session packs into 7 chunks, against a 600s chain budget
and a 300s per-call cap — so a large session still times out and parks. "A
cycle with ingress will produce an accepted atom" is therefore not yet true,
and the soak cannot be assumed to accrue simply by waiting.

A correctness note for whoever attests this gate next: `health.eligible` is
assigned from `notes_created` (`dream/orchestrator.py:112`) and is therefore
the count of atoms produced in that cycle — **not** an ingress signal. Use
`passes.atomize.split_sessions > 0` for "a new ingress session", together with
`passes.atomize.slices > 0` for "at least one accepted atom". The index-coverage
`eligible` figure is a different number entirely
(`runtime/indexes/retrieval.coverage.json`, currently 946/946).

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
