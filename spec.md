# Puppetmaster Discord Integration Brief

## Purpose

Expose a Puppetmaster root orchestrator as a personal Discord bot so a human can send prompts from Discord and receive intentional orchestrator replies back in the same Discord channel.

The integration is a transport adapter over Puppetmaster's existing service, registry, tmux, hook, and MCP architecture. Discord must not replace the durable registry or make Codex Stop hooks depend on Discord availability.

## Product Shape

The bot runs as:

```bash
puppet discord serve
```

It connects to one configured Discord guild and one explicit Puppetmaster state directory. In v1, the Discord token and guild id live in local `.puppetmaster/config.toml`.

Discord channels are bound to root orchestrators with slash commands. Once bound, the channel becomes that orchestrator's Discord room.

## Core Decisions

- The outbound tool is frontend-neutral: `send_human_message(message)`.
- The orchestrator never receives or chooses Discord channel ids.
- Outbound routing is by root orchestrator binding.
- One root orchestrator has one active Discord channel binding.
- Bindings are whole text channels only.
- `/puppet bind` accepts root orchestrator ids only.
- Inbound prompts require either a bot mention or a reply to a bot-authored message.
- Plain channel chatter is ignored.
- The inbound prompt format is:

```text
DISCORD MESSAGE RECEIVED:
<message>
```

- Attachments are ignored in v1.
- Slash commands use plain text/code-block formatting.
- Long Discord output is chunked up to a cap: `chunk_size = 1900`, `max_chunks = 3`.
- Successful prompt delivery uses a low-noise reaction.
- Delivery failures get visible replies.
- The bot shows typing while orchestrator work is pending.
- Typing stops when `send_human_message` is delivered, root `agent.turn_stopped` is observed, or `typing_timeout_seconds` expires.
- Stop hooks continue writing durable registry events only. The Discord bot polls SQLite instead of receiving hook IPC.

## Configuration

Example local config:

```toml
[discord]
token = "..."
guild_id = "..."
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

`.puppetmaster/` is local state and must stay ignored by git because it contains the Discord token.

## Slash Commands

Initial command set:

```text
/puppet agents
/puppet bind agent_id:<root-agent-id>
/puppet unbind
/puppet status
/puppet read lines:<optional>
/puppet tree
```

Behavior:

- `/puppet agents` lists root orchestrators only.
- `/puppet bind` binds the current text channel to a root orchestrator.
- `/puppet unbind` removes the current channel binding.
- `/puppet status` shows the bound root orchestrator state.
- `/puppet read` reads recent output from the bound root orchestrator only.
- `/puppet tree` shows the full agent tree under the bound root and requires a binding.

Guild-scoped slash commands sync on bot startup.

## Bidirectional Flow

Inbound:

```text
Discord mention/reply
-> bot validates channel binding
-> bot strips bot mention
-> bot sends prompt_agent(root, "DISCORD MESSAGE RECEIVED:\n<message>", source="discord")
-> bot reacts on success
-> bot starts typing indicator
```

Outbound:

```text
orchestrator calls send_human_message(message)
-> MCP tool resolves caller root
-> service requires root Discord binding
-> service enqueues outbound_human_messages row
-> Discord bot poll loop sends message to bound channel
-> bot marks row delivered or failed
-> bot stops typing for that root on success
```

Turn stop:

```text
Codex Stop hook records agent.turn_stopped
-> Discord bot poll loop observes root turn stop after latest Discord prompt
-> bot stops typing quietly
```

## Durable State

Add SQLite state for:

- Discord channel bindings: `channel_id -> root_agent_id`.
- Outbound human messages: pending/delivered/failed queue.

Typing state is intentionally in memory. It is best-effort UI feedback and does not need to survive bot restart.

## Safety Model

This is a personal local tool, not a multi-tenant service. There is no per-user authorization in v1.

Cheap safety rails still apply:

- The bot only operates in the configured guild.
- A channel must be explicitly bound to a root orchestrator.
- Prompt messages must mention or reply to the bot.
- The orchestrator cannot choose arbitrary Discord destinations.

This matters because Puppetmaster-managed Codex sessions can run with bypassed approvals and sandboxing. A bound Discord channel is effectively a remote control surface for local code execution.

## Milestones

- `008-discord-config-and-state`: config, bindings, outbound queue.
- `009-human-message-tool`: service and MCP `send_human_message`.
- `010-discord-bot-commands`: Discord client, slash commands, binding UX.
- `011-discord-bidirectional-runtime`: inbound prompts, outbound dispatch, typing lifecycle.
- `012-discord-hardening-and-docs`: errors, recovery, docs, release validation.
