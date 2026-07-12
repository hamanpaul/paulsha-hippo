---
name: multi-model-orchestration
description: Use when running a large multi-batch implementation across many issues/PRs where a single expensive model would hit session/rate limits, when you need a foreign-model adversarial gate before merge, or when driving copilot/codex CLIs as agent labor inside a Workflow. Keywords: ultracode, multi-model, copilot driver, codex gate, heterogeneous review, workflow resume, session limit, fail-closed merge.
---

# Multi-Model Orchestration

## Overview

Run a large implementation (many issues → many PRs) as a **Workflow** where different models do different jobs by their comparative advantage, and **no code merges without passing a foreign-model adversarial gate**.

Core insight: **the factory and the inspector must be different models.** Same-model self-review shares the same blind spots — three same-model lenses still miss what one foreign model catches. Put the expensive frontier model on the critical path only for hard fixes; push bulk labor to cheaper/other-vendor models; make a foreign model (Codex) the merge gate.

**Two load-bearing invariants (violating either defeats the purpose):**
1. **Heterogeneous gate is fail-closed.** A foreign-model reviewer's blocking finding blocks merge — no same-model majority can override it. If the gate is absent (timeout/error), say so explicitly; never silently merge as if it passed.
2. **Resume, don't restart.** Long runs WILL hit session/rate limits. The Workflow journal caches completed agents; re-invoke to continue at zero re-cost. Never re-run merged batches.

## When to Use

- A backlog of independent-ish issues, each a PR-sized change, that a single model can't finish before hitting limits.
- You want an independent (different-vendor) adversarial review as a hard merge gate, not advisory.
- You want to offload implement/mechanical work to copilot CLI or codex CLI while keeping orchestration in the Workflow.
- The user opted into multi-agent orchestration (said "ultracode", "use a workflow", or invoked a skill that calls Workflow).

**When NOT to use:** a single PR or a few files (just do it inline); work with no natural batch decomposition; when the user hasn't opted into workflow-scale spend.

**REQUIRED BACKGROUND:** the `Workflow` tool contract (pipeline/parallel/agent, journal, resume). This skill assumes you author Workflow scripts.

## Model Roster (assign by comparative advantage, not by prestige)

| Role | Model | Why |
|---|---|---|
| Orchestrator (main loop) | frontier (e.g. Fable/Opus) | Holds the plan, dispatches, reads gates, decides topology. Cheap in tokens — mostly tool calls. |
| Bulk implement (per-task TDD) | copilot CLI `gpt-5.4` (via driver) | High-volume mechanical labor; different vendor conserves frontier quota. |
| Driver / verify / mechanical | `sonnet` subagents | Drives copilot, runs full test suite, 3-lens verify, ship/merge, closeout. Good judgment, cheaper than frontier. |
| Trivial setup | `haiku` subagents | Worktree checks, cleanup — near-zero judgment. |
| **Merge gate (adversarial)** | **Codex `gpt-5.6-sol` (foreign)** | The whole point: catches what same-model review can't. |
| Hard fixes | frontier (main loop only) | Security/correctness fixes flagged by the gate — the one place to spend the best model. |

Set per-agent model in the Workflow via `agent(prompt, { model: 'sonnet' | 'haiku' | ... })`. The main loop's own model is set by the session (`/model`); when the frontier model's limit resets, switch the session model to keep going — subagents inherit the new session model unless pinned.

**Rule of thumb:** the frontier model should only ever touch (a) orchestration and (b) fixes the foreign gate demands. Everything else is someone else's job. When the frontier limit is hit mid-run, that's a signal you leaned on it too hard — move more to sonnet/copilot next round.

## The Batch Pipeline (per PR, in its own git worktree)

Each batch runs these gates in order; failing any gate stops the batch (downstream batches that depend on it are skipped and reported, never force-merged):

1. **worktree** (haiku) — create from latest `origin/main`, or (resume) verify existing worktree + drop `.psc_tmp` residue.
2. **implement** (copilot driver, sonnet) — one task at a time, TDD, per the plan. See `copilot-driver-contract.md`.
3. **full test** (sonnet) — whole suite + policy/lint green, small fixes allowed.
4. **3-lens verify** (sonnet ×3 parallel) — correctness / policy / regression. **fail-closed: any lens's blocking finding stops the batch**, no majority vote. ≤2 fix rounds.
5. **foreign gate** (sonnet forwarder → Codex) — adversarial review of the branch diff. Blocking findings → fix (frontier) → re-gate. Absent (timeout) → report explicitly, fall back to lens results + human sign-off. See `codex-gate-contract.md`.
6. **ship** (sonnet) — rebase onto latest main (resolve conflicts by the plan's merge rules), re-run full verify, open PR (`Closes #N`), squash-merge, remove worktree.

Topology is expressed with plain promises: independent batches start together; dependent batches `await` their upstream's merged result. Batches that touch the same file must be **serialized**, not merely both-await-a-common-ancestor (parallel edits to one file collide at rebase).

A generalized, runnable script is in `workflow-template.js`.

## Boundaries & Behavior Constraints (non-negotiable)

- **Foreign gate is fail-closed and un-overridable.** No same-model majority overrules a foreign blocking finding. This is the reason the architecture exists.
  - **"The reviewer might be wrong, so let the author triage the findings" is the trap that voids the gate.** A blocking finding is resolved exactly two ways: (a) fix the code and pass a fresh re-gate, or (b) the *foreign reviewer itself* withdraws it on re-review. The author-side model (Sonnet/frontier that wrote or drove the code) does NOT get to judge a foreign finding a false positive and merge over it — that is same-vendor override wearing a "technical judgment" hat. Genuine false positives are cheap to disprove: fix or clarify, then re-gate. If you're arguing with the finding instead of re-gating, stop.
- **Gate absence is surfaced, never hidden.** If Codex times out and even the resume-fallback fails, the batch is marked "gate absent" and merge requires explicit human sign-off — the PR body must say the gate was absent.
- **No `git add -A` in implement agents.** Copilot and subagents stage only files the task touched. Test-harness residue (`.psc_tmp/`, `__pycache__`) must never enter version control. Add such paths to `.git/info/exclude`. (Learned the hard way: bulk `git add` committed hundreds of temp files and stalled a whole re-run on a dirty worktree check.)
- **Squash-merge only.** Per-task commits and any residue stay out of main history.
- **Recovery/stateful ops touch real state — quiesce first, back up first, verify restore.** Any step that mutates live runtime (not code) stops all writers, takes a verified-restorable backup, gates on a real probe, and halts on the first failed verification without rolling back merged code.
  - **Verify process ownership before you SIGTERM anything.** A process that *looks* like a stray orphan may belong to another project or another running agent (a different tool's daemon, a sibling worktree's job). Read its `cmdline`/`cwd` and confirm it's yours before killing — never SIGTERM by name-pattern alone. (This run: two orphan-looking `start.sh` loops were a different project's bot and another agent's worktree; killing by pattern would have taken down live work.)
- **Merge ≠ deploy.** Landing PRs on main does NOT update a *deployed* runtime — a pipx/venv/container install keeps running the old code until reinstalled. Any recovery or verification that exercises the *deployed* system (not the repo checkout) must redeploy from the merged SHA first, then prove the new code is actually live (assert a newly-added CLI command/symbol exists in the *installed* copy, from a neutral cwd so a repo-root import can't shadow it). Skip this and the recovery runs old code and "fails" for a phantom reason. **Pitfall:** `pipx reinstall <pkg>` reinstalls the *original pinned spec* (e.g. `git+…@<old-sha>`) — it pulls the OLD code. Deploy the new code explicitly: `pipx install --force <spec>@<new-sha>` (or from the local repo path).
- **Conditional close.** A PR only carries `Closes #N` when the issue's acceptance is fully evidenced. Missing evidence → `Refs #N` + split the remainder into a new issue; never auto-close on partial delivery.
- **"Running" is not "proven" — a live metric that's still zero blocks the close.** Deploying the machinery and watching it emit the *first* funnel stage (e.g. `offered`) is not proof of the whole funnel. If the outcome metric is still zero in production (`read=0`, `applied=null`), the feature is not done even though the code shipped and CI proved the mechanism. The pull to "it's running now, close it" is strong and will be voiced — resist it. And do NOT manufacture the evidence (hand-read a slice, hand-emit an `applied` event) just to make the number non-zero; that is the exact hand-crafted echo the gate exists to reject. Real outcome evidence accrues from real usage over time, or from a hermetic E2E test — not from a staged one-off.
- **Honest capability claims.** If a probe can't prove a capability (e.g. a CLI's hook actually fired), mark it inconclusive/produce-only — don't upgrade an unverified path to "supported".

## Quick Reference

| Situation | Action |
|---|---|
| Frontier model limit hit mid-run | `/model` to another tier; re-invoke Workflow with `resumeFromRunId` — completed agents replay from cache |
| A batch's fix was done outside the workflow | re-invoke with a `skipImplement`-style flag so it re-runs gates only, not implement |
| Batch already merged in a prior run | hard-code it as `Promise.resolve({merged:true})` so the next run doesn't redo it |
| Codex model rejected ("not supported on this account") | check `~/.codex/models_cache.json` for the real available ids; don't guess |
| Codex review hangs | forwarder cancels after a timeout, then `task --resume` asks for the conclusion it already formed |
| Worktree dirty on resume | `rm -rf <wt>/.psc_tmp` then check only for non-`??` (tracked) changes |
| Same file edited by two batches | serialize the batches (A→C→D), don't run them in parallel |
| Runtime still broken after all PRs merged | merge≠deploy: redeploy the merged SHA (`pipx install --force <spec>@<new-sha>` — plain `pipx reinstall` pulls the old pinned SHA), then assert a new CLI command exists in the installed copy |
| Foreign gate finds a NEW issue after you fixed the last one | expected — keep going; the onion peels (see Real-World Impact). Don't stop until a re-gate round is clean |

## Monitoring the run

- **Live:** `/workflows` shows the progress tree; each `agent()` label (`A:task3:copilot`, `E:codex-gate`) maps to a node.
- **Post-mortem / debugging an empty or surprising result:** read `<transcriptDir>/journal.jsonl` — one `{"type":"result",...}` line per completed agent with its full return value. Cross-reference `started` lines (which carry the agent label→id map) to attribute a finding to a batch/lens. Do NOT `cat`/tail the per-agent `agent-<id>.jsonl` transcripts into your context — they overflow it.
- **Copilot progress:** the driver subagent is your monitor. It runs copilot under `timeout`, then verifies (commit exists, tests pass, diff matches intent) and retries/takes-over on failure. See `copilot-driver-contract.md`.

## Common Mistakes

- **Same-model gate.** Three Claude lenses ≠ a foreign review. If the gate isn't a different vendor, you don't have this architecture — you have expensive self-review.
- **Silent merge on gate timeout.** Defeats the whole point. Absence must block-or-escalate, never pass.
- **Restarting instead of resuming.** Burns the whole run's tokens again. Always `resumeFromRunId`; hard-code already-merged batches.
- **Frontier model on bulk work.** You'll hit the limit and stall the critical path. Push implement/verify/mechanical to copilot/sonnet/haiku.
- **`git add -A` anywhere.** Commits harness residue; a later run's clean-worktree check then blocks. Stage named files only.
- **Parallel batches on one file.** Rebase collision. Serialize file-overlapping batches.
- **Trusting a passing local suite as the gate.** The foreign reviewer repeatedly found real defects (secret-redaction bypass, non-atomic publish, silent YAML truncation, lost-update races) that a fully-green suite missed. The suite is necessary, not sufficient.

## Real-World Impact

Across one multi-day run (9 issues → 6 PR batches), the foreign gate peeled defects layer by layer that same-model 3-lens verify + a green test suite had passed: e.g. a requeue path was fixed four times (add terminal state → purge poison cache → gate on fragment presence → validate fragment frontmatter/session ownership), each deeper defect surfaced only by the foreign reviewer. Zero half-baked PRs reached main; every blocked PR was a real defect. Then a `pipx install --force @<merged-sha>` deploy (a plain `pipx reinstall` would have pulled the old pinned SHA) plus a live-probe backend gate let the recovery sequence heal a 3-months-broken pipeline: index coverage went 42% → 100% (indexed==eligible) and ~80 stuck sessions drained to a terminal state, verified by live metrics after a cold-start reboot.

**What convergence looks like (so you don't stop early or panic).** Each re-gate round confirms the *prior* round's fixes are genuinely resolved AND surfaces a *deeper* one; findings get rarer and more subtle each round (terminal-state → poison-cache → fragment-gate → frontmatter/ownership → concurrent-rollback → hard-abort crash-consistency), not "clean by round 2." Two things that feel alarming but are normal: (1) a fix can *introduce* a new defect the same gate then catches (a rollback fix that adds a concurrency hazard) — that's the gate working, not a process regression; (2) it takes several rounds. Keep going until one full re-gate round is clean. The alternative — merging because "we've fixed enough" — is exactly what the architecture exists to prevent.
