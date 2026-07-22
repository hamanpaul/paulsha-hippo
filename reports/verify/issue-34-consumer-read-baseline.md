# Issue #34/#39 Consumer Read Baseline

Status: passed (pre-candidate baseline only)

## Scope

This evidence proves that the currently installed Claude hook path can produce a real same-session `offered → Read` event. It does not attest a future candidate commit or wheel and therefore does not satisfy the final release closure gate by itself.

## Execution

- Executed at: 2026-07-22T03:21:50Z–2026-07-22T03:22:01Z
- Tool: `claude-code`
- Project: `paulshaclaw`
- Session ID: `78dd68fd-5165-496b-9fff-bf4cb60a4960`
- Permission boundary: `Read` only; no permission bypass
- Agent outcome: completed successfully

Sanitized invocation shape:

```bash
claude --print \
  --session-id '<uuid>' \
  --output-format json \
  --max-turns 4 \
  --tools Read \
  --allowedTools Read \
  --permission-mode default \
  '<consumer Read canary prompt>'
```

## Ledger evidence

The installed `UserPromptSubmit` hook appended one `offered` event containing three slices. The installed `PostToolUse(Read)` hook then appended, under the same session:

```json
{
  "session_id": "78dd68fd-5165-496b-9fff-bf4cb60a4960",
  "tool": "claude-code",
  "project": "paulshaclaw",
  "sl_id": "sl-ce70efcaa1ff0d19",
  "source": "read",
  "offered": true
}
```

The aggregate funnel moved as follows:

| Metric | Before | After |
|---|---:|---:|
| sessions | 17 | 18 |
| total reads | 1 | 2 |
| Claude offered | 77 | 80 |
| Claude reads | 1 | 2 |

## Rerun and verification

```bash
hippo usage --memory-root "$HOME/.agents/memory" --json

jq -c --arg sid '<session-id>' \
  'select(.session_id == $sid)' \
  "$HOME/.agents/memory/runtime/ledger/offered.jsonl"

jq -c --arg sid '<session-id>' \
  'select(.session_id == $sid and .source == "read" and .offered == true)' \
  "$HOME/.agents/memory/runtime/ledger/memory_usage.jsonl"
```

Final Issue #34/#39 closure still requires the same installed-client trace after the candidate commit and wheel SHA-256 are frozen, plus the remaining OpenSpec release gates.
