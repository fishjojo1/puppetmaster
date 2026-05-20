# Implementation Plan

## Inbound Message Handling

In `discord_bot.py`, add `on_message` handling.

Ignore:

- Messages from the bot itself.
- Messages outside the configured guild.
- Direct messages.
- Threads.
- Channels without a Discord binding.
- Slash command interactions.
- Messages that neither mention the bot nor reply to a bot message.

Accept:

- A text message in a bound text channel that mentions the bot.
- A text message in a bound text channel that replies to a message authored by the bot.

Mention stripping:

- Remove direct mentions of the bot from message content.
- Trim whitespace.
- Do not include Discord author/channel metadata in the prompt.

Prompt format:

```text
DISCORD MESSAGE RECEIVED:
<cleaned message>
```

Attachments:

- Ignore all attachments.
- If cleaned text is empty, reply: `I only accept text messages right now.`
- Do not download attachment files.
- Do not include attachment URLs.

Delivery:

- Resolve bound root agent id from channel binding.
- Call `prompt_agent(registry, tmux, root_agent_id, prompt, source="discord")`.
- On success, add a checkmark reaction to the triggering Discord message.
- On failure, reply with a short error message.
- On success, start or refresh typing state for that root/channel.

## Typing Tracker

Maintain in-memory typing state:

```python
active_typing[root_agent_id] = {
    "channel_id": str,
    "prompt_delivered_at": str,
    "timeout_at_monotonic": float,
    "task": asyncio.Task,
}
```

Behavior:

- Start typing after a Discord prompt is delivered.
- If another prompt arrives for the same root before completion, refresh the timestamp and timeout.
- Continue typing until stopped.
- Stop typing when:
  - an outbound human message for the same root is successfully delivered to Discord;
  - the poll loop sees root `agent.turn_stopped` after `prompt_delivered_at`;
  - `typing_timeout_seconds` elapses.
- Stop quietly on turn stop or timeout.

Implementation:

- Use `channel.typing()` in a small loop.
- Because direct `await channel.typing()` lasts roughly 10 seconds, refresh until stopped.
- Keep one typing task per root.
- Cancel and clean up tasks on bot shutdown.

## Outbound Dispatcher

Add a background poll loop in the Discord bot.

Every `poll_interval_seconds`:

1. Query `registry.pending_outbound_human_messages("discord", limit=20)`.
2. For each pending row:
   - Fetch channel by `channel_id`.
   - If channel is missing, mark failed with a clear error.
   - Send message using chunked plain text helpers.
   - Mark delivered after all chunks send.
   - Mark failed if Discord send raises.
3. If delivery succeeds, stop typing for that message's root id.

Failure behavior:

- Do not crash the bot on one failed message.
- Persist failure in `outbound_human_messages`.
- Log enough context for manual debugging.

Ordering:

- Send pending messages ordered by `created_at`.
- Preserve queue order per root as much as practical.

## Turn Stop Polling

In the same or a separate poll loop:

- For every active typing root, check whether the registry has an `agent.turn_stopped` event for that root after `prompt_delivered_at`.
- If yes, stop typing quietly.

If no helper exists, add a small registry method:

```python
latest_event_time(agent_id: str, event_type: str) -> str | None
```

If it already exists, reuse it.

## Concurrency

The Discord bot is async while registry methods are synchronous.

Keep v1 simple:

- Registry calls are small and local SQLite operations.
- Avoid long blocking loops.
- Use short polling intervals and small batch sizes.
- If blocking becomes visible, move registry calls to `asyncio.to_thread` in a later hardening milestone.

## Non-Goals

- No per-user authorization.
- No slash command embeds.
- No attachment download.
- No queueing of inbound prompts while busy.
- No local HTTP/socket notifier from Stop hooks.
