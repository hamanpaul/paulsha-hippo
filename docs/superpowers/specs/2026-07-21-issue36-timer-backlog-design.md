# Issue #36: Dream Timer Enable + Backlog Reconciliation

**Date**: 2026-07-21
**Issue**: [#36](https://github.com/hamanpaul/paulsha-hippo/issues/36)
**OpenSpec change**: `issue-34-atomization-release` (§8 new section)

## Context

Issue #36 identifies two overlapping bugs:

**Bug A — Timer never enabled**: `hippo install service --enable` created a
`hippo-dream.timer` unit file (`WantedBy=timers.target`) but it sat inactive for
6 days until an unrelated `daemon-reload` passively activated it. `LastTriggerUSec`
remained `n/a` (never fired). The deployed timer's schedule (`daily 03:00` +
`RandomizedDelaySec=900`) does not match any version in the repo — the template
history is `Mon..Fri 05:00` → `hourly`, never had `RandomizedDelaySec`.

**Bug B — Atomize can't read `_slices` backlog**: Manual `hippo dream run` showed
`atomize: slices=0, split_sessions=0` despite 1959 fragment files in
`inbox/_slices/`. Root cause is a ledger ↔ filesystem desync: sessions were marked
terminal (`promoted`/`no-findings`/`parked`) but fragments were never archived,
or the processing ledger lost track of split sessions entirely.

### A3 root cause analysis: `NEXT=07-24` anomaly

- 07-21 = Tuesday, 07-24 = Friday.
- If `OnCalendar=*-*-* 03:00:00` (daily), next after 07-21 08:14 should be
  07-22 (Wed) 03:00. But `NEXT` = 07-24 (Fri) — skipping Wed and Thu.
- Most consistent explanation: the deployed `OnCalendar` is `Fri 03:00`
  (weekly Friday), not daily. The issue author likely misread it.
- `Persistent=true` did not catch up because the timer had never triggered
  (`LastTriggerUSec=n/a`). systemd's `Persistent=true` requires a prior trigger
  timestamp to compute catch-up; without one, it only schedules the next future
  slot.
- The deployed timer has `RandomizedDelaySec=900` which no repo version ever had.
  Conclusion: deployed timer unit was manually modified and diverges from repo.

## Decision: merge into issue-34-atomization-release

#36's tasks are added as a new §8 section in the existing
`openspec/changes/archive/2026-07-22-issue-34-atomization-release/tasks.md`, keeping #36's
timer+backlog coherence in one block. All §8 tasks ship with v0.1.1.

## Design

### 8.1 — Installer enable post-verification

**Where**: `paulsha_hippo/ops.py`, `run_install_service()` (after line 677).

After `systemctl --user enable --now paulsha-hippo-dream.timer`:

1. `systemctl --user is-active paulsha-hippo-dream.timer` → must be `active`
2. `systemctl --user is-enabled paulsha-hippo-dream.timer` → must be `enabled`
3. If either fails: return 1 + diagnostic message
4. If both pass: print baseline `LastTriggerUSec` (expected `n/a` on first install)

Retry once (0.5s sleep) on `is-active` if systemd hasn't caught up.

Unchanged: unit file write, rename logic, daemon-reload, linger reminder,
`enable=False` path (no verification), systemd unavailable early return.

### 8.2 — Doctor timer health + unit drift detection

**Where**: `paulsha_hippo/ops.py`, doctor section (after line 260).

**`_check_timer_health()`**:
- `systemctl --user show paulsha-hippo-dream.timer --property=ActiveState,LastTriggerUSec,NextElapseUSecRealtime,UnitFileState`
- `LastTriggerUSec=n/a` or stale (>2x OnCalendar period) → WARN "timer 從未觸發 / 已 stale"
- `NextElapseUSecRealtime` gap >2x OnCalendar period → WARN "next elapse 異常遠"
- `UnitFileState=disabled` → WARN "timer 未 enable"
- All healthy → print `healthy` + last trigger + next elapse
- Returns `(healthy: bool, messages: list[str])`

**`_check_timer_unit_drift()`**:
- Read deployed `~/.config/systemd/user/paulsha-hippo-dream.timer`
- Compare against repo template (post-rename expected content)
- Diff only the key fields: `OnCalendar`, `Persistent`, `Description`, `WantedBy`
- **Exclude `ExecStart`** from comparison — installer substitutes `sys.executable` at install time (environment-dependent: pipx/venv/global), so ExecStart legitimately differs across installs. Comparing it would produce false positives.
- Any difference in key fields → WARN "部署 timer unit 與 repo template 不同步" with details
- Report only, never overwrite
- Returns `(drifted: bool, messages: list[str])`

Both functions called from doctor main loop. systemd unavailable → fallback
message, no crash, no exit code impact.

### 8.3 — Reconcile dry-run (diagnosis report)

**Where**: `paulsha_hippo/dream/reconcile.py` (new module),
`paulsha_hippo/dream/cli.py` (new `reconcile` subcommand).

**CLI arguments** (consistent with existing `dream run` / `dream status`):
- `--memory-root` (required) — memory root path, same as all dream subcommands
- `--now` (required) — timestamp string, same as `dream run`
- `--dry-run` (flag) — produce report only (default when neither `--dry-run` nor `--apply` is given)
- `--apply` (flag) — execute fixes
- `--limit N` (optional int) — max N sessions **per category** (so up to 4×N total across orphan/terminal/stale/healthy; healthy is a no-op so effectively 3×N). Default unlimited.

`hippo dream reconcile --dry-run` (also default when no mode flag):

1. `inbox/_slices/**/*.md` full rglob → group by `{agent}__{session}__` prefix
2. `processing.fold_events(memory_root)` → all session ledger states
3. Cross-reference and classify:

| Category | Condition | Suggested action |
|---|---|---|
| `orphan_fragment` | fragment exists, ledger has no session | set `state=split` |
| `terminal_unarchived` | ledger ∈ {promoted, no-findings}, fragment still in `_slices` | archive fragment |
| `stale_split` | ledger=split, fragment missing | mark `no-findings` |
| `healthy` | ledger=split + fragment exists | no action needed |

Category names use underscores as canonical form in code and JSON; display
output may use hyphens for readability.

Output JSON with summary counts + per-session details + suggested actions:

```json
{
  "summary": {"orphan_fragment": 42, "terminal_unarchived": 15, "stale_split": 3, "healthy": 120, "malformed": 1},
  "details": [
    {"session_key": "claude:abc123", "category": "orphan-fragment", "fragments": 5, "action": "set-split"},
    ...
  ]
}
```

### 8.4 — Reconcile apply (backlog fix)

`hippo dream reconcile --apply`:

- Run dry-run classification first, then execute per category:
  - `orphan-fragment`: `processing.append_state(memory_root, session_key=session_key, state="split", now=now, config_hash="reconcile", source="reconcile", fragments=frag_count)` → next dream run's `_promote_pass` processes it
  - `terminal-unarchived`: import and call `_archive_fragments()` from `atomizer/pipeline.py` (read-only dependency — reconcile imports the function, does not modify that module) → move to `archive/fragments/`
  - `stale-split`: `processing.append_state(memory_root, session_key=session_key, state="no-findings", now=now, config_hash="reconcile", source="reconcile", no_findings_reasons=["fragments missing"])`
- Each fix writes dream ledger record via `dream_ledger.append_run()` with `reconcile` marker in the record's `passes` dict (key `"reconcile"` → value is a dict `{"applied": N, "errors": M, "categories": {...}}`)
- `--limit N`: max N sessions per category (default unlimited)

**Safety gates**:
- Acquire dream singleton lock (`dream_lock.acquire_dream_lock`) — skip if held
- Bare `hippo dream reconcile` (no flag) → defaults to `--dry-run`
- Per-session failure → record error, continue next session, summary includes `errors` count
- All ledger mutations via `processing.append_state` (existing atomic writes), never raw file edits. `config_hash="reconcile"` is a fixed sentinel string — if downstream code validates `config_hash` as hex, this sentinel must be added to any allowlist. Reconcile-originated events are identifiable via `source="reconcile"` field.
- Does not touch `atomizer/pipeline.py` — reconcile only fixes ledger + archives, doesn't run atomize

### Error handling & edge cases

**Doctor timer health**:
- `systemctl --user show` fails → print "timer 健檢不可用", no crash, no exit code impact
- Deployed timer unit file missing → print "timer unit 未安裝", skip drift check
- `LastTriggerUSec` parse failure → print raw value + WARN, no crash
- Unknown `OnCalendar` format → skip reasonableness check, report raw value only

**Installer enable verification**:
- `is-active`/`is-enabled` `systemctl` call fails → return 1 + stderr summary
- Timer just created, systemd hasn't registered yet → retry once (0.5s sleep)

**Reconcile**:
- `_slices` directory missing → empty report (all 0), normal exit
- Fragment frontmatter unparseable → classify as `malformed`, skip on `--apply` with WARN, no crash
- `fold_events` fails → abort reconcile, print error (no guessing)
- `--apply` per-session failure → record error, continue, summary includes `errors` count
- Dream lock unavailable → print "dream lock held, reconcile skipped", exit 0

### Interaction with existing #34 tasks

- 8.3/8.4 reconcile does not touch `atomizer/pipeline.py` — no overlap with §2 session content preservation
- 8.2 doctor timer check added after existing `_systemd_user_available()` block — no overlap with §3.3 doctor attestation (that is per-surface version/build, this is timer effectiveness)
- 8.1 installer verification added at end of `run_install_service` — no overlap with §3.2 staged upgrade runner (that is multi-surface upgrade flow, this is single timer enable post-check)

## Testing strategy

**8.1 — `tests/test_ops.py`**:
- mock `subprocess.run`: enable success + is-active=active + is-enabled=enabled → return 0
- enable success + is-active=inactive → return 1 + diagnostic
- `enable=False` → no verification (regression)
- systemd unavailable → early return 0 (regression)

**8.2 — `tests/test_ops.py`**:
- mock `systemctl --user show`: `LastTriggerUSec=n/a` → WARN "從未觸發"
- stale timestamp (>2x period) → WARN "stale"
- deployed timer `OnCalendar` differs from template → WARN drift
- deployed unit file missing → "未安裝", drift skipped
- systemd unavailable → fallback message (regression)

**8.3/8.4 — `tests/test_dream_reconcile.py` (new)**:
- fixture: fragment exists, ledger empty → `orphan-fragment`
- fixture: ledger=promoted, fragment still present → `terminal-unarchived`
- fixture: ledger=split, fragment missing → `stale-split`
- fixture: ledger=split + fragment exists → `healthy`
- `_slices` missing → empty report
- malformed frontmatter → `malformed` category
- `--apply` orphan-fragment → `append_state(split)` → dream run digests it
- `--apply` terminal-unarchived → fragment moved to archive, `_slices` clean
- `--apply` stale-split → `append_state(no-findings)`
- `--limit N` → only first N processed
- dream lock held → skip exit 0
- partial failure → continue, summary with errors count

All tests use existing patterns (subprocess.run mock + memory_root tmp_path fixture).
No new test framework introduced.
