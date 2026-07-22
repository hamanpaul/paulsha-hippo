# Issue #34 / #39 release closeout

Status: complete

## Pinned release candidate

- Commit: `eb2ccb86d7d4c4a91a8f8e2c0a743a677e52b2b1`
- Version: `0.1.1`
- Wheel: `paulsha_hippo-0.1.1-py3-none-any.whl`
- Wheel SHA-256: `b895ef91ab0e7ebb1836779b9c5664e2f32ed6e847e0e4af89fa3de82e5dcc6a`
- Build manifest SHA-256: `33a6ed2fa459dc9ba15194d689f23cda2cb930232f577ad5b765debe8aab585b`
- Release: <https://github.com/hamanpaul/paulsha-hippo/releases/tag/v0.1.1>

The wheel was built once from the pinned merge commit. All installed, recovery,
scheduled-soak, consumer, and publication evidence below binds to that commit
and wheel; the release artifact was not rebuilt during metadata closeout.

## Candidate and installed-surface gates

| Gate | Evidence |
|---|---|
| Test truth | Exact-merge full suite: 1,535 passed, 4 skipped, 151 subtests; candidate rollback/recovery/config/upgrade subset: 74 passed. |
| PR truth | PR #53 pytest and policy checks passed; Copilot review completed with no comments and no unresolved review thread. |
| Package identity | Clean wheel install reported version `0.1.1`, commit `eb2ccb86…`; packaged atomizer config, skill, install manifest, and hook assets were present outside the checkout. |
| Installed identity | pipx package, copied hooks, and systemd service all attest commit `eb2ccb86…`; timer is enabled and active. |
| Force install | `hippo install all --force` completed twice; the second pass was idempotent. A later dry-run returned `mutation=false`, no conflicts, and install-manifest SHA-256 `5a068c6b8057f00a2cfadcdf48916ba1a0afb2bfa85e097d61678f89565ccb39`. |
| Canonical config | Migration dry-run/apply/second-plan preserved canonical config SHA-256 `4bb58bbb6d550936561015c6b2dd46ac226c2a76b4447ff57cb12803ded1c2e8`, reported no prohibited field or conflict, and made no semantic mutation. |
| Upgrade profiles | Both `current-pipx` and `stale-system` prepared write-ahead schema-v2 transactions pinned to the release wheel hash. An invalid command plan failed closed before mutation. |
| Rollback/compensation | Eleven exact-candidate rollback, concurrent-edit, target-drift, replay-after-compensation, and external-phase failure tests passed in 0.76 seconds. |
| External profiles | Installed Claude and Codex profile probes completed with non-empty output; service-effective doctor attested the two Tier-1 profiles. |
| Fallback | Synthetic corpus first received invalid Claude output, advanced to Codex, and published one atom as `degraded-success` with both attempts retained. |
| Semantic corpus | Run `dream-2026-07-22T15:00:56.442260Z` produced one retrieval-eligible atom with canonical title/project, valid provenance/build identity, zero excluded output, and complete 1/1 index coverage. |
| Policy/OpenSpec | Active change strict validation passed. Before tag, policy had 24 passes and only the expected R-07 version-vs-tag failure plus one pre-existing R-22 advisory; after publication R-07 passes. |

## Production recovery and no-data-loss evidence

- Recovery ID: `e7e09cd89e1dabfeb3a1`
- Manifest SHA-256: `b87dcd0c2dac0ab640a3d004516a16191a75fb723858396b62dfd281fe962833`
- Journal SHA-256: `53f102053c43ef1e31fffdbad2dd8e41a6870420ecb41a379590c42289f7a64f`
- Source authority: 1,455 frozen captures, including 636 baseline captures and
  819 audited ingress-drift captures; 888 canonical logical-session winners.
- Disposition: all 888 winners are `importer-recover` and committed; all 567
  non-winner captures are explicitly `superseded-source`; no unexplained source
  or logical-session winner remains. This over-approximates and therefore fully
  enumerates the 53-session high-risk PR #35 cohort rather than relying on an
  unversioned side list.
- Transaction journal: 178 `batch_started`/`batch_done` pairs and, for every
  winner, exactly one `begin`, `preimage`, `staged`, `replace_intent`,
  `replaced`, and `committed` event. There is no error or rollback event.
- Empty resume returned complete with zero new commit, proving idempotent
  recovery completion. Historical append-only ledgers were not truncated.
- Post-recovery reconciliation had zero orphan/stale action, and independent
  index verification was complete. The final scheduled-soak index census is
  recorded below.

## Scheduled systemd soak

The controlled recovery and pre-soak drain initially established
`parked=92`, `quarantined=4`, `split=0`. The 00:00 timer cycle was healthy, but
the next 01:00 timer cycle encountered one intended canary plus three unrelated
pending sessions,
failed closed as `partial`, and parked all four. Neither diagnostic run counts
toward the final consecutive soak. After that visible failure, independent
index verification remained complete at 571/571 and the formal restart baseline
was fixed at `parked=96`, `quarantined=4`, `split=0`.

| Cycle | Timer run ID | Unique canary session | Accepted / excluded | Failure state | Index |
|---|---|---|---:|---|---|
| 1 | `dream-2026-07-22T18:00:03.977069Z` | `copilot-cli:f9bfa505-c603-48c0-a28e-3f0eca90bc70` | 1 / 0 | 96 / 4 / 0 | 572 / 572 |
| 2 | `dream-2026-07-22T19:00:03.968341Z` | `copilot-cli:7f5ff8bb-6f14-435c-b54b-6f3c6f97d1cf` | 1 / 0 | 96 / 4 / 0 | 573 / 573 |
| 3 | `dream-2026-07-22T20:00:03.970540Z` | `copilot-cli:57409351-e954-4d3e-bb1c-0ef9a7c77961` | 2 / 0 | 96 / 4 / 0 | 575 / 575 |

All counted runs are executions of the enabled
`paulsha-hippo-dream.timer`, not direct service starts. Each run must attest the
pinned build, finish `status=ok`, publish at least one accepted canary atom,
produce zero excluded notes, keep the failure-state baseline unchanged, and
finish with `eligible == indexed` and no index problem. Earlier 22:00 and 23:00
timer attempts correctly reported `skipped: system busy` under `--require-idle`;
they are operational evidence for the unchanged load gate and are not counted.

## Installed consumer offered-to-Read

- Candidate-bound Claude session: `e7dd72a7-3b94-4840-b405-55e94bed3ba3`
- Installed UserPromptSubmit hook offered three paths for project `paulshaclaw`.
- The same session used the real `Read` tool on
  `sl-5e9ca642a8bca3b6`.
- Installed PostToolUse hook appended `source=read` and `offered=true` under the
  same session. No `applied` claim is inferred from text.
- The canary was limited to the Read tool. Its SessionEnd producer capture was
  intentionally suppressed with the shipped `HIPPO_SELF_SESSION` recursion
  guard so consumer validation could not inject another Dream source during the
  scheduled soak.

This is the required pinned-candidate rerun of the pre-candidate baseline in
`reports/verify/issue-34-consumer-read-baseline.md`.

## Issue #34 nine-row closure map

| Original row | Closure |
|---|---|
| 1. Consumer funnel | Candidate-bound same-session offered-to-Read passed through installed Claude hooks. |
| 2. Dream never green | Three consecutive counted timer cycles finish `ok`; skipped busy cycles remain honest skips. |
| 3. Legacy long filename | NAME_MAX-safe target/temp naming, crash/concurrency tests, controlled recovery, and post-recovery index verification close the path. |
| 4. Parked/split recovery | Bounded categorized fallback/parking is implemented; authority-manifest recovery commits all 888 canonical winners; soak shows no growth. |
| 5. Backend drift | Canonical config is the only runtime authority; external CLI profiles own credentials and endpoints; installed surface identity is attested. |
| 6. Generic title / unknown project | Canonical LLM title repair, source-project authority, rich project identity plus hashed directory key, grounded proposal gate, and synthetic corpus evidence close both defects. |
| 7. Malformed inbox | Durable quarantine and complete health counts replace repeated warning-only loops; quarantine stays stable during soak. |
| 8. Copilot ingress | Installed Copilot SessionEnd importer produced each release canary, and each identified session reached an accepted atom. |
| 9. Legacy locks | Dry-run reports no legacy or unknown lock; runtime retains only 64 shard locks and named global locks. |

## Issue #39 acceptance closure

- Runtime distillation, title generation, and SkillOpt use the same external CLI
  router. Hippo has no direct provider transport or credential lifecycle.
- Profiles carry typed traits/task classes, tier/priority, requested
  model/effort, tokenized `shell=False` argv, stdin prompt transport, safety
  eligibility, and deterministic fallback policy.
- Tier-1 Claude/Codex, Tier-2 agy/cg, and Tier-3 local launchers are represented;
  alias-only or unavailable executables fail closed.
- Fallback, cache isolation, provenance, deadline/budget, single-park, unsafe
  config, and environment-minimization contracts are covered by the candidate
  tests and the live Claude-to-Codex degraded-success drill.
- Manifest-driven `--force` reconciles only owned surfaces, protects memory and
  credential paths, fences writers/services, supports compensation, and is
  idempotent in the installed candidate.
- Both supported upgrade profiles, installed profile probes, three scheduled
  Dream canaries, and exact published-wheel smoke are release gates documented
  in this record.

## Publication and metadata boundary

- `v0.1.1` was absent locally, remotely, and from GitHub Releases immediately
  before publication.
- The immutable tag points to `eb2ccb86…`; GitHub Release assets match the
  frozen wheel and manifest hashes above.
- A fresh GitHub-asset download matched wheel SHA-256 `b895ef91…` and manifest
  SHA-256 `33a6ed2f…`; a clean venv outside the checkout reported version `0.1.1`,
  commit `eb2ccb86…`, and a `site-packages/paulsha_hippo` import root.
- Downstream immutable pin update merged through
  <https://github.com/hamanpaul/paulshaclaw/pull/263> as merge commit
  `bd31dfd3a55d2e01521719ef5ef1dd9954a5bdeb`.
- This report, the completed readiness matrix, task checkboxes, and official
  OpenSpec archive are a post-tag metadata-only change. They do not alter or
  rebuild the released artifact.
