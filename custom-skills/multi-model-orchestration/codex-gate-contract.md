# Codex Adversarial Gate Contract

How a Workflow runs Codex (`gpt-5.6-sol`) as the **foreign-model merge gate**. Codex runs via the codex-companion runtime (a Node script shipped with the Codex plugin), driven by a `sonnet` **forwarder subagent**. Codex is a different vendor from the Claude factory — that difference is the entire value.

## Companion runtime

The companion script lives under the Codex plugin cache; resolve its path once (do NOT hardcode a personal absolute path in committed artifacts):

```
COMPANION="$(find "$HOME/.claude/plugins/cache" -name codex-companion.mjs -path '*codex*' | head -1)"
```

Key subcommands (all `node "$COMPANION" <cmd>`):
- `task-resume-candidate --json` — is there a resumable thread from this session?
- `task [--fresh|--resume] [--model <id>] [--effort <lvl>] "<text>"` — run a task turn.
- `adversarial-review [--background] [--base <ref>] "focus: …"` — challenge review of a diff.
- `status <id>` / `cancel <id>` — poll / cancel a background job.

## Model id gotcha

The account's available Codex models are enumerated in `~/.codex/models_cache.json`. Requesting an unavailable id fails hard:
`The '<id>' model is not supported when using Codex with a ChatGPT account.` Observed: the desired `gpt-5.6-sol` was the config default while a mistaken `gpt-5.6-codex-sol` was rejected. **Read the cache file for the real ids; don't guess from the config or from memory.**

## Forwarder subagent contract (the gate step)

A `sonnet` agent runs and monitors the review:

1. `cd "$WT"`, launch background review:
   ```
   node "$COMPANION" adversarial-review --background --base origin/main \
     "focus: 審本 branch 相對 origin/main 的完整 diff，對照 plan 與 spec，挑戰實作正確性與驗收達成。"
   ```
2. Grab the task id, poll `status <id>` every ~60s until `completed`/`failed` or a **45-minute** cap.
3. `completed` → read the review, distil `[high]`-level items into `blocking_findings[]`.
4. **Timeout fallback:** `cancel <id>`, then `task --resume "上一輪 review 掛住了，請直接輸出你已形成的結論"` once (≈10-min cap) — Codex has usually already formed the verdict; this recovers it cheaply. (In practice a 55-min hang was recovered exactly this way.)
5. All recovery failed → return `absent: true`. **Absence is reported, never treated as a pass.**
6. Return `{verdict, blocking_findings: string[], absent}`.

## How the orchestrator uses the verdict (fail-closed)

- `blocking_findings.length > 0` and not absent → dispatch a **frontier** fix agent for exactly those findings → **re-gate** (a fresh review of HEAD). Loop until clean or a bounded retry.
- `absent` → do NOT merge silently. Mark the batch gate-absent; merge requires explicit human sign-off and the PR body must disclose it.
- clean → proceed to ship.

No same-model lens or majority can override a Codex blocking finding. If Codex says high-severity, it blocks.

## Why this earns its cost (observed)

A fully-green local suite + same-model 3-lens verify still passed defects that Codex caught: a secret-redaction path a user policy override could disable (PATs into ledgers), a DB published non-atomically before its coverage sidecar (violating "old index preserved on failure"), unquoted YAML interpolation that a standard parser silently truncates while the repo's own parser stayed green, and a lost-update race where a lock covered only the final write but not the read-modify-append pipeline. Each was a real, reproducible bug behind a green build. The gate is where those die.

## Plan-level pre-gate (cheaper, earlier)

Run one Codex `adversarial-review` on the **plans** before any implementation starts. Upstream interception is far cheaper than catching the same design flaw six PRs deep. Fold its blocking findings into the plans first.
