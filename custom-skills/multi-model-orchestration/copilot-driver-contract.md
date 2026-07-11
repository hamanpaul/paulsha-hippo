# Copilot CLI Driver Contract

How a Workflow drives GitHub Copilot CLI (`gpt-5.4`) as bulk implement labor, and how the driver **monitors** it. Copilot is a headless CLI that edits files and runs commands; it is NOT a Workflow agent, so a `sonnet` **driver subagent** wraps each invocation: brief → run → verify → retry/take-over.

## Why a driver instead of calling copilot directly

Copilot is fire-and-forget headless: it runs to completion (or a timeout) and prints a final message; there is no mid-run status/cancel API like the Codex companion has. So monitoring = **a supervising subagent that verifies the result and owns the fallback.** The driver is the model that can read the diff, judge whether the task was actually done, and finish the job itself if copilot stalls.

## Copilot invocation (verified working)

```bash
cd "$WT" && timeout 1800 copilot \
  --model gpt-5.4 \
  -p "$(cat "$BRIEF_FILE")" \
  -s \
  --allow-all-tools \
  --add-dir "$WT"
```

- `-p/--prompt <text>` — non-interactive; copilot exits after completion.
- `-s/--silent` — print only the agent's final response (scriptable).
- `--allow-all-tools` — REQUIRED for non-interactive; without it copilot blocks on permission prompts and hangs to timeout.
- `--add-dir "$WT"` — grant file access to the worktree.
- `--model gpt-5.4` — pin the tier. Use `auto` to let copilot choose.
- Wrap in `timeout` (≈1800s) — copilot has no self-cancel; the wrapper is your only bound.
- Pass the brief via a temp **file** (`$(cat …)`), not a giant inline arg — long CJK prompts break shell quoting.

## Known reliability caveat (route around it)

Copilot's stdin handling of `-p` payloads was observed unreliable for large content (content dropped, agent wanders to timeout). Mitigations that held up:
- Keep each invocation scoped to **one plan task**, not a whole plan.
- Put the full task text in the brief file; keep the shell arg short.
- The driver's verify+takeover (below) is the real safety net — treat copilot as best-effort labor, not a guaranteed worker.

## Driver subagent contract (this is the monitoring)

The driver is a `sonnet` agent. Per plan task it does:

1. **Brief** — read the plan's `### Task N` section. Write a brief file to scratch containing: the working dir, a TDD instruction (failing test → FAIL → minimal impl → PASS → commit per the plan's message), the full task text, the drift note (plan line numbers are advisory; anchor by content), the test-runner note (`PYTHONPATH=. python3 -m pytest`), and **"stage only files this task touched — never `git add -A`; never commit `.psc_tmp`/`__pycache__`."**
2. **Run** — the copilot invocation above.
3. **Verify (the monitor step)** — after copilot exits, check ALL of:
   - a new commit exists (`git log origin/main..HEAD`), else copilot didn't finish;
   - the task's tests actually PASS (run them, don't trust the transcript);
   - `git status --porcelain` has no non-`??` residue and no stray staged temp files;
   - the diff matches the task's intent (spot-read key files).
4. **Retry once** — on verify failure, append the failure evidence to the brief and re-feed copilot a single time.
5. **Take over** — still failing? The driver implements the task itself via TDD (it has full tool access). Bulk labor is delegated, not abdicated — the batch must not stall on a weak worker.
6. **Return** `{ok, detail}` — `detail` says whether copilot finished it, finished on retry, or the driver took over, plus a test summary.

## Return-schema discipline

The driver returns a small validated object (`{ok: boolean, detail: string}`) so the Workflow can branch deterministically. Copilot's own prose is NOT the return value — the driver distills it. This keeps the orchestrator's decisions off unstructured text.

## Failure attribution

If a batch stalls in implement, the journal's `*:task*:copilot` result `detail` tells you which task and whether it was copilot or the driver-takeover that failed — so the next resume can target that task, not re-run the whole batch.
