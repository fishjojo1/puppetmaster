# Validation Plan

## Automated Tests

Inbound prompt tests with fake Discord message objects:

- Plain message in a bound channel is ignored when it does not mention or reply to the bot.
- Mentioned message in a bound channel is delivered to `prompt_agent`.
- Reply-to-bot message in a bound channel is delivered.
- Mention text is stripped before prompt formatting.
- Prompt is exactly `DISCORD MESSAGE RECEIVED:\n<message>`.
- Message with only mention and attachments is rejected with text-only error.
- Attachments are ignored and not included in the prompt.
- Unbound channel mention receives setup hint.
- Prompt delivery failure creates a visible reply.
- Prompt delivery success adds a reaction.

Outbound dispatcher tests:

- Pending outbound messages are sent to the bound channel.
- Long outbound messages use capped chunking.
- Successful delivery marks the queue row delivered.
- Failed delivery marks the queue row failed with error text.
- One failed message does not prevent later pending messages from being processed.
- Delivery stops active typing for the root.

Typing tests:

- Prompt delivery starts typing state.
- A second prompt for the same root refreshes timestamp and timeout.
- Root `agent.turn_stopped` after prompt timestamp stops typing.
- Root `agent.turn_stopped` before prompt timestamp does not stop new typing.
- Timeout stops typing.
- Outbound human message delivery stops typing.

## Manual End-To-End Checks

Prerequisites:

- Discord bot token and guild id in `.puppetmaster/config.toml`.
- A running root orchestrator.
- Bot running with `puppet discord serve`.
- Discord channel bound with `/puppet bind`.

### Mention Prompt

In the bound channel:

```text
@Puppetmaster say hello back with send_human_message
```

Expected:

- Bot adds a checkmark reaction.
- Bot shows typing.
- Orchestrator receives:

```text
DISCORD MESSAGE RECEIVED:
say hello back with send_human_message
```

- Orchestrator calls `send_human_message`.
- Bot posts the reply.
- Typing stops.
- Outbound queue row is marked delivered.

### Reply Prompt

Reply to one of the bot's messages:

```text
now summarize your status
```

Expected:

- Bot delivers the prompt even without a mention.
- Bot reacts and shows typing.

### Plain Chatter

Send a plain message without mention or reply.

Expected:

- Bot ignores it.
- No `agent.prompted` event is created.

### Turn Stop Without Reply

Mention the bot with an instruction that causes the orchestrator to finish without calling `send_human_message`.

Expected:

- Typing starts.
- When root `agent.turn_stopped` is recorded, typing stops quietly.
- No Discord "finished without reply" message is posted.

### Timeout

Temporarily set `typing_timeout_seconds = 5`.

Send a prompt that does not produce a reply or turn stop.

Expected:

- Typing stops after about 5 seconds.
- No visible timeout message is posted.

## Acceptance Criteria

- The Discord channel is genuinely bidirectional.
- Bot mentions/replies reach the bound root orchestrator.
- Orchestrator `send_human_message` calls are delivered back to Discord.
- Typing indicator reflects pending orchestrator work and stops correctly.
- Stop hooks remain durable registry writers and do not depend on Discord IPC.
