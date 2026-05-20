# Implementation Plan

## Scope

This milestone adds the service and MCP surface for outbound human messages. It relies on the Discord state added in milestone 008, but it does not implement the Discord bot dispatcher.

## Service Function

Add `send_human_message` to `src/puppetmaster/services.py`.

Signature:

```python
def send_human_message(registry: Registry, agent_id: str, message: str, *, source: str = "mcp_tool") -> dict[str, Any]:
    ...
```

Behavior:

1. Load the caller agent with `registry.get_agent(agent_id)`.
2. Resolve `root_id = agent["root_id"]`.
3. Trim or validate `message`.
4. Reject empty messages with `PuppetError("message_required", ...)`.
5. Look up `registry.discord_binding_for_root(root_id)`.
6. If missing, raise `PuppetError("no_human_channel", "No Discord channel is bound for this root orchestrator.", ...)`.
7. Enqueue an outbound human message:
   - `root_agent_id = root_id`
   - `agent_id = agent_id`
   - `transport = "discord"`
   - `channel_id = binding["channel_id"]`
   - `message = message`
8. Append an audit event to the caller agent:
   - type: `human.message.queued`
   - source: `source`
   - summary: `Human message queued.`
   - payload: message id, transport, channel id, message length
9. Return a small result:
   - `queued: True`
   - `id`
   - `transport`
   - `channel_id`

Security and simplicity:

- Do not accept channel ids in the tool.
- Do not let the orchestrator choose transport.
- Do not queue messages for unbound roots.
- Do not attempt Discord delivery inside the MCP tool.

## MCP Tool

Expose `send_human_message(message: str)` in `src/puppetmaster/mcp_server.py`.

Tool docstring:

```text
Send a concise message to the bound human operator channel. Use this when replying to a human request received through Puppetmaster.
```

Implementation:

- Use `_context()` to identify the caller.
- Call the service function with `caller["id"]`.
- Return service result.
- Catch `PuppetError` and return `_error(exc)`.

## Prompt Text

Update `prompt_text` in `src/puppetmaster/services.py` so managed agents know about the tool.

Add to the generic Puppetmaster tools section:

```text
- Use send_human_message to send a concise response to the human operator when a human-facing reply is needed.
```

Add to the orchestrator event loop section:

```text
- When you receive a DISCORD MESSAGE RECEIVED prompt, answer the human by calling send_human_message. Do not include Discord channel ids or transport details.
```

Keep the text short. The orchestrator should not be confused by Discord implementation details.

## CLI Debug Helper

Optional but useful: add an internal/debug CLI command:

```bash
puppet debug enqueue-human-message --agent <agent-id> --message "..."
```

This may call the service directly and helps test the outbound queue before the Discord bot exists. If added, keep it under `debug`, not as a primary operator command.

## Non-Goals

- No Discord posting.
- No slash commands.
- No typing indicator.
- No inbound Discord message handling.
