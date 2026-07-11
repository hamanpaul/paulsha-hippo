# Workflow Harness Mechanics

The `Workflow` tool runs a JS script in the background that orchestrates `agent()` calls. These are the mechanics that make a long, limit-prone, multi-round run survivable.

## Journal & resume (the survival mechanism)

- Every `Workflow` invocation writes a **journal** at `<transcriptDir>/journal.jsonl` and returns a `runId` (`wf_…`).
- Each `agent()` call is keyed by a hash of its `(prompt, opts)`. On resume, an **unchanged** call replays its cached result instantly; the first changed/new call and everything after runs live.
- **Resume:** re-invoke `Workflow({ scriptPath, resumeFromRunId })`. Same script + same args → ~100% cache hit; you only pay for what changed.
- **Iterate cheaply:** the script is persisted to a file on every run (path returned in the result). Edit that file, re-invoke with `scriptPath` — don't resend the whole script inline.

## Reading results

- The tool result's `<result>` is the script's return value (may be truncated — the full copy is in the task output file).
- `journal.jsonl`: `{"type":"result", "agentId", "result"}` per finished agent, plus `{"type":"started", "agentId"}` lines. The label→id map is on the `started` side; join on `agentId` to attribute a `result` to its labelled step.
- **Before diagnosing an empty/surprising result, read the journal** — a cached result can itself be empty; don't assume.
- Never `cat` the per-agent `agent-<id>.jsonl` transcripts into context; they overflow it. The journal's `result` lines are the digest you want.

## Surviving session / rate limits

Long runs hit them. Two complementary defenses:

1. **Model tiering** keeps the frontier model's usage tiny (orchestration + hard fixes only), so its limit is hit last, if at all. When it IS hit, the failing agent is the only casualty — everything already merged stays merged.
2. **Resume** picks up exactly where it stopped. When the frontier limit resets or you `/model` to a different tier, re-invoke with `resumeFromRunId`; merged batches are already recorded and skipped.

**Between-run state hand-off:** when a batch was merged (or fixed outside the workflow) in a prior run, edit the script so that batch is a `Promise.resolve({merged:true, …})` (or gets a `skipImplement` flag). This makes the topology reflect reality and stops the next run redoing landed work. This is normal, expected editing between rounds — the run is a sequence of converging invocations, not one shot.

## Determinism constraints (scripts are plain JS)

- No `Date.now()` / `Math.random()` / argless `new Date()` — they break resume. Vary agents by index/label; stamp timestamps after the run.
- Plain JS only (no TS types). Standard built-ins are available; no filesystem/Node API from the script body (do file ops inside `agent()` via Bash).
- `parallel([...thunks])` is a barrier that never rejects — a failed thunk resolves to `null`; `.filter(Boolean)` before use.
- Concurrency is capped (~cpu-2, ≤16); pass all items, they queue.

## Topology as promises

Express dependencies with plain promise chaining, not a DSL:

```js
const pA = runBatch('A')                 // independent, starts now
const pB = Promise.resolve({merged:true})// already landed in a prior run
const pC = pA.then(a => a.merged ? runBatch('C') : {merged:false, blocked:'upstream A'})
const pRecovery = Promise.all([pA, pB]).then(([a,b]) =>
  (a.merged && b.merged) ? runRecovery() : {merged:false, blocked:'upstream'})
```

Independent batches run concurrently; dependents await; **file-overlapping batches must be a serial chain** (A→C→D), because parallel edits to one file collide at rebase even if both "await A".

## Worktree hygiene on resume

- Test harnesses may drop residue (`.psc_tmp/`, `__pycache__`) into worktrees. Add them to `.git/info/exclude`.
- A resume's worktree check must ignore untracked residue: `rm -rf "$wt/.psc_tmp"`, then only fail on non-`??` (tracked) changes. A naive "porcelain must be empty" check will wrongly block a clean branch buried under temp dirs.
- `gh pr merge --delete-branch` can fail its local branch-delete when the primary repo has `main` checked out in another worktree; the remote squash-merge still succeeds — delete the remote branch explicitly and move on.
