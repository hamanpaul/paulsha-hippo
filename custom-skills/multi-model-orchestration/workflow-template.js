// Multi-model orchestration Workflow template.
// Generalized from a real 9-issue / 6-PR run. Adapt BATCHES + topology to your work.
// Roles by model: haiku=setup, sonnet=driver/verify/mechanical, copilot gpt-5.4=implement,
// Codex gpt-5.6-sol=foreign merge gate, frontier(main loop)=hard fixes only.
//
// Pass this file to the Workflow tool via {scriptPath}. Edit + re-invoke with
// {scriptPath, resumeFromRunId} to continue a run at zero re-cost.

export const meta = {
  name: 'multi-model-batches',
  description: 'Multi-model batch pipeline: copilot implement, sonnet verify, Codex foreign gate, fail-closed auto-merge',
  phases: [
    { title: 'PR-A' }, { title: 'PR-B' }, { title: 'PR-C' },
    { title: 'Recovery' }, { title: 'Closeout' },
  ],
}

// --- resolve tool paths without hardcoding personal absolute paths ---
// (In a real run, compute COMPANION inside an agent via:
//   find "$HOME/.claude/plugins/cache" -name codex-companion.mjs -path '*codex*' | head -1)
const COMPANION = '<codex-companion.mjs path — resolve at runtime, do not hardcode a personal path>'
const WT_BASE = '<worktrees base dir>'   // e.g. a sibling dir of the repo
const REPO = '<primary repo path>'

const DRIFT = 'Plan line numbers are advisory; anchor by content. Adapt to current code, note deviations.'
const TESTNOTE = 'Run tests in the worktree with PYTHONPATH=. python3 -m pytest (avoid editable-install pointing at main repo).'

// One entry per PR batch. `phase` groups its agents in /workflows.
const BATCHES = {
  A: { plan: 'docs/plans/pr-a.md', branch: 'feature/a', tasks: 12, closes: 'Closes #15', phase: 'PR-A', title: 'fix: batch A' },
  B: { plan: 'docs/plans/pr-b.md', branch: 'feature/b', tasks: 7,  closes: 'Closes #16', phase: 'PR-B', title: 'fix: batch B' },
  C: { plan: 'docs/plans/pr-c.md', branch: 'feature/c', tasks: 7,  closes: 'Closes #19', phase: 'PR-C', title: 'fix: batch C' },
}

const STEP = { type: 'object', properties: { ok: { type: 'boolean' }, detail: { type: 'string' } }, required: ['ok', 'detail'] }
const VERIFY = { type: 'object', properties: {
  blocking: { type: 'array', items: { type: 'object', properties: { file: {type:'string'}, issue: {type:'string'}, fix_hint: {type:'string'} }, required: ['file','issue','fix_hint'] } },
  notes: { type: 'array', items: { type: 'string' } } }, required: ['blocking','notes'] }
const GATE = { type: 'object', properties: {
  verdict: { type: 'string' }, blocking_findings: { type: 'array', items: { type: 'string' } }, absent: { type: 'boolean' } },
  required: ['verdict','blocking_findings','absent'] }

async function runBatch(key, opts = {}) {
  const b = BATCHES[key], wt = `${WT_BASE}/${key.toLowerCase()}`, ph = b.phase
  const skipImplement = !!opts.skipImplement   // set when the batch was fixed outside the workflow

  // 1. worktree (haiku — trivial)
  const setup = await agent(
    skipImplement
      ? `rm -rf ${wt}/.psc_tmp, then verify worktree ${wt} on branch ${b.branch}, >=${b.tasks} commits ahead of origin/main, and NO tracked changes (ignore untracked ?? residue). Return {ok, detail}.`
      : `In ${REPO}: git fetch origin main; git worktree add ${wt} -b ${b.branch} origin/main (recreate if exists; delete stale branch first). Confirm ${wt}/${b.plan} exists. Return {ok, detail}.`,
    { label: `${key}:worktree`, phase: ph, schema: STEP, model: 'haiku', effort: 'low' })
  if (!setup?.ok) return { key, merged: false, blocked: `worktree: ${setup?.detail}` }

  // 2. implement — copilot gpt-5.4 driven by a sonnet driver, one task at a time (skipped on resume-of-fixed)
  if (!skipImplement) {
    for (let t = 1; t <= b.tasks; t++) {
      const r = await agent(
        `You DRIVE copilot CLI to complete "### Task ${t}" of ${wt}/${b.plan} (only Task ${t}), in worktree ${wt}.
1. Read the Task section. Write a brief file to scratch: working dir + TDD instruction (failing test→FAIL→minimal impl→PASS→commit per plan) + the full Task text + "${DRIFT}" + "${TESTNOTE}" + "stage ONLY files this task touched; never git add -A; never commit .psc_tmp/__pycache__".
2. Run: cd ${wt} && timeout 1800 copilot --model gpt-5.4 -p "$(cat <brief>)" -s --allow-all-tools --add-dir ${wt}
3. VERIFY (monitor): new commit exists; the task's tests actually PASS; no non-?? residue; diff matches intent.
4. Verify fails → append evidence, re-feed copilot ONCE.
5. Still failing → YOU implement it via TDD yourself.
Return {ok, detail(copilot-done / retry-done / driver-takeover + test summary)}.`,
        { label: `${key}:task${t}:copilot`, phase: ph, schema: STEP, model: 'sonnet' })
      if (!r?.ok) return { key, merged: false, blocked: `Task ${t}: ${r?.detail}` }
    }
  }

  // 3. full suite (sonnet)
  const test = await agent(
    `In ${wt}: PYTHONPATH=. python3 -m pytest -q and the repo's policy/lint check. All green → {ok:true}. Reds → fix per test intent (commit), ≤2 rounds, else {ok:false, detail}. ${TESTNOTE}`,
    { label: `${key}:fulltest`, phase: ph, schema: STEP, model: 'sonnet' })
  if (!test?.ok) return { key, merged: false, blocked: `fulltest: ${test?.detail}` }

  // 4. 3-lens verify (sonnet ×3) — FAIL-CLOSED: any lens blocking stops the batch
  const LENSES = [['correctness','logic/edge/state-machine holes vs plan+spec acceptance'],
                  ['policy','changelog/lint/no personal paths/commit format/docs sync'],
                  ['regression','breakage of existing tests/behavior/shared interfaces']]
  for (let round = 0; round <= 2; round++) {
    const v = await parallel(LENSES.map(([n, d]) => () =>
      agent(`In ${wt}: adversarially review git diff origin/main...HEAD through ONLY the "${d}" lens, vs ${wt}/${b.plan}. Report only confident blocking issues. Return {blocking:[{file,issue,fix_hint}], notes:[]}.`,
        { label: `${key}:verify:${n}:r${round}`, phase: ph, schema: VERIFY, model: 'sonnet' })))
    const blocking = v.filter(Boolean).flatMap(x => x.blocking)
    if (blocking.length === 0) break
    if (round === 2) return { key, merged: false, blocked: `3-lens: ${blocking.length} blocking after 2 rounds` }
    const fixed = await agent(`In ${wt}: fix these blocking findings (test + commit): ${JSON.stringify(blocking)}. Return {ok, detail}.`,
      { label: `${key}:fix:r${round}`, phase: ph, schema: STEP })   // main-loop (frontier) model — a hard fix
    if (!fixed?.ok) return { key, merged: false, blocked: `fix: ${fixed?.detail}` }
  }

  // 5. foreign gate (sonnet forwarder → Codex) — fail-closed, absence surfaced
  let gate = await agent(
    `Codex gate forwarder. cd ${wt}. Launch: node "${COMPANION}" adversarial-review --background --base origin/main "focus: challenge this branch's diff vs plan ${b.plan}". Poll status every 60s to completed/failed or 45min. Completed→distil [high] blocking_findings. Timeout→cancel, then task --resume "output the conclusion you already formed" once (10min). All fail→absent:true. Return {verdict, blocking_findings, absent}.`,
    { label: `${key}:codex-gate`, phase: ph, schema: GATE, model: 'sonnet' })
  if (gate && !gate.absent && gate.blocking_findings.length > 0) {
    const fixed = await agent(`In ${wt}: fix Codex blocking findings (test + commit): ${gate.blocking_findings.join('\n')}. Return {ok, detail}.`,
      { label: `${key}:codex-fix`, phase: ph, schema: STEP })       // frontier — hard fix
    if (!fixed?.ok) return { key, merged: false, blocked: `codex-fix: ${fixed?.detail}` }
    gate = await agent(`Codex gate forwarder (re-gate). cd ${wt}. Re-run adversarial-review on HEAD, same protocol. Return {verdict, blocking_findings, absent}.`,
      { label: `${key}:codex-regate`, phase: ph, schema: GATE, model: 'sonnet' })
    if (gate && !gate.absent && gate.blocking_findings.length > 0)
      return { key, merged: false, blocked: `codex re-gate still blocking: ${gate.blocking_findings.join('; ')}` }
  }
  const codexAbsent = !gate || gate.absent

  // 6. ship (sonnet): rebase → re-verify → PR → squash-merge → remove worktree
  const ship = await agent(
    `In ${wt} on ${b.branch}, serially (stop+return ok:false on any failure):
1. git fetch origin main && git rebase origin/main (resolve conflicts by the plan's no-whole-line-overwrite merge rule).
2. PYTHONPATH=. pytest -q green + policy check clean + changelog fragment present.
3. git push -u origin ${b.branch} --force-with-lease.
4. gh pr create --title "${b.title}" ${b.labels ? `--label ${b.labels} ` : ''}--body "${b.closes}${codexAbsent ? ' (NOTE: Codex gate ABSENT — merged on lens results, disclose)' : ' (Codex foreign gate passed)'}".
5. gh pr merge --squash --delete-branch.
6. cd ${REPO} && git worktree remove ${wt} --force.
Return {ok, detail(PR url + merge SHA)}.`,
    { label: `${key}:ship`, phase: ph, schema: STEP, model: 'sonnet' })
  return ship?.ok ? { key, merged: true, detail: ship.detail, codexAbsent } : { key, merged: false, blocked: `ship: ${ship?.detail}`, codexAbsent }
}

// --- topology: independent batches concurrent; dependents await; file-overlapping = serial chain ---
const pA = runBatch('A')
const pB = runBatch('B')
const pC = pA.then(a => a.merged ? runBatch('C') : { key: 'C', merged: false, blocked: 'upstream A blocked' })

// Recovery / stateful ops (if any) await their code batches and touch REAL state — quiesce, back up, verify.
const pRecovery = Promise.all([pA, pB]).then(([a, b]) => {
  if (!a.merged || !b.merged) return { merged: false, blocked: `upstream A=${a.merged} B=${b.merged}` }
  return agent(`In ${REPO} (main now has A+B). Run the recovery sequence: gate on a real probe FIRST (abort if it fails); quiesce all writers; take a verified-restorable backup; do the mutation; verify each step; halt on first failure WITHOUT rolling back merged code. Return {ok, detail(evidence + backup path)}.`,
    { label: 'recovery', phase: 'Recovery', schema: STEP, model: 'sonnet' }).then(r => r?.ok ? { merged: true, detail: r.detail } : { merged: false, blocked: r?.detail })
})

const [a, b, c, rec] = await Promise.all([pA, pB, pC, pRecovery])
const all = { A: a, B: b, C: c }
log(`batches: ${Object.entries(all).map(([k, v]) => `${k}=${v.merged ? 'OK' : 'X'}`).join(' ')} recovery=${rec.merged ? 'OK' : 'X'}`)

// Closeout: only close issues whose batch merged; partial → Refs + split; report gate-absent PRs.
const closeout = await agent(
  `In ${REPO}. Closeout: for MERGED batches confirm auto-close of their issues; for blocked batches leave an honest status comment, do NOT close. Conditional-close rule: full evidence → Closes; partial → Refs + open a follow-up issue. Batch results: ${JSON.stringify(Object.fromEntries(Object.entries(all).map(([k, v]) => [k, { merged: v.merged, note: v.detail || v.blocked }])))}. recovery=${rec.merged}. Return {ok, detail}.`,
  { label: 'closeout', phase: 'Closeout', schema: STEP, model: 'sonnet' })

return { batches: all, recovery: rec, closeout }
