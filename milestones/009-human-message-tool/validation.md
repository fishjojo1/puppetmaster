# Validation Plan

## Automated Tests

Service tests:

- `send_human_message` rejects an empty message.
- It rejects a caller whose root has no Discord binding with `no_human_channel`.
- It enqueues a pending outbound message when the caller root is bound.
- It uses the caller agent id and caller root id correctly.
- A child agent call routes through the child root binding, not through the child id.
- It appends a `human.message.queued` audit event.
- It never accepts or records a caller-provided channel id.

MCP tests:

- Tool returns a normal queued result on success.
- Tool returns an error dictionary when unbound.
- Tool returns an error dictionary for empty message.

Prompt text tests:

- Generated orchestrator prompt mentions `send_human_message`.
- Generated orchestrator prompt mentions `DISCORD MESSAGE RECEIVED`.
- Prompt text does not expose channel id routing instructions.

## Manual Checks

Start a root orchestrator or create a raw root for service-level testing.

Bind a fake Discord channel through a temporary script or test helper:

```python
reg.bind_discord_channel("123456789", "<root-agent-id>", "987654321")
```

Call the MCP/service path or optional debug command:

```bash
PYTHONPATH=src python3 -m puppetmaster.cli debug enqueue-human-message \
  --agent <root-agent-id> \
  --message "Hello from the orchestrator."
```

Inspect registry state:

```bash
PYTHONPATH=src python3 -m puppetmaster.cli debug registry --json
```

Expected:

- One `pending` outbound Discord human message exists.
- The message references the bound channel id.
- The agent has a `human.message.queued` event.

## Acceptance Criteria

- Managed agents can queue human-facing replies without Discord-specific arguments.
- Missing binding is a visible tool error.
- No outbound message is lost silently.
- Existing MCP tools continue to work.
